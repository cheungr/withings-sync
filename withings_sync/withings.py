"""Withings OAuth and weight-measurement API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
import uuid

import requests

from withings_sync.storage import Storage

AUTHORIZE_URL = "https://account.withings.com/oauth2_user/authorize2"
TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
MEASURE_URL = "https://wbsapi.withings.net/measure"
TOKEN_FILE = "withings.json"


class WithingsError(RuntimeError):
    """Raised when a Withings operation cannot complete."""


@dataclass(frozen=True)
class Measurement:
    source_id: str
    timestamp: datetime
    weight: float
    percent_fat: float | None
    percent_hydration: float | None
    bone_mass: float | None
    muscle_mass: float | None
    bmi: float | None
    visceral_fat: float | None = None


def _scaled_value(measure: dict[str, Any]) -> float | None:
    value = measure.get("value")
    unit = measure.get("unit", 0)
    if value is None:
        return None
    try:
        result = float(value) * (10 ** int(unit))
        if not math.isfinite(result):
            raise ValueError("measurement must be finite")
        return round(result, 2)
    except (TypeError, ValueError, OverflowError) as exc:
        raise WithingsError(f"Invalid measurement value: {measure!r}") from exc


def parse_measurement_groups(
    groups: list[dict[str, Any]], height_m: float | None = None
) -> list[Measurement]:
    result = []
    for group in groups:
        try:
            timestamp = datetime.fromtimestamp(int(group["date"]), timezone.utc)
            measures = {
                int(item["type"]): _scaled_value(item)
                for item in group.get("measures", [])
            }
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise WithingsError(f"Invalid Withings measurement group: {group!r}") from exc
        weight = measures.get(1)
        if weight is None or weight <= 0:
            continue
        bmi = (
            round(weight / (height_m**2), 1)
            if height_m and math.isfinite(height_m) and height_m > 0
            else None
        )
        hydration = measures.get(77)
        result.append(
            Measurement(
                source_id=str(
                    group.get("grpid")
                    or hashlib.sha256(
                        json.dumps(group, sort_keys=True).encode("utf-8")
                    ).hexdigest()
                ),
                timestamp=timestamp,
                weight=weight,
                percent_fat=measures.get(6),
                percent_hydration=(
                    round(hydration * 100 / weight, 2) if hydration is not None else None
                ),
                bone_mass=measures.get(88),
                muscle_mass=measures.get(76),
                bmi=bmi,
                visceral_fat=measures.get(170),
            )
        )
    return sorted(result, key=lambda measure: (measure.timestamp, measure.source_id))


class WithingsClient:
    def __init__(
        self,
        storage: Storage,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
        session: requests.Session | None = None,
    ):
        self.storage = storage
        self.client_id = client_id or os.getenv("WITHINGS_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("WITHINGS_CLIENT_SECRET", "")
        self.redirect_uri = redirect_uri or os.getenv("WITHINGS_REDIRECT_URI", "")
        self.session = session or requests.Session()

    def _require_app(self) -> None:
        if not all((self.client_id, self.client_secret, self.redirect_uri)):
            raise WithingsError(
                "Set WITHINGS_CLIENT_ID, WITHINGS_CLIENT_SECRET, and "
                "WITHINGS_REDIRECT_URI in .env before authenticating."
            )

    def authorization_url(self, state: str) -> str:
        self._require_app()
        return f"{AUTHORIZE_URL}?{urlencode({'response_type': 'code', 'client_id': self.client_id, 'state': state, 'scope': 'user.metrics', 'redirect_uri': self.redirect_uri})}"

    @staticmethod
    def extract_code(value: str, expected_state: str | None = None) -> str:
        value = value.strip()
        if value.startswith(("http://", "https://")):
            query = parse_qs(urlparse(value).query)
            code = query.get("code", [""])[0]
            returned_state = query.get("state", [None])[0]
            if expected_state and returned_state != expected_state:
                raise WithingsError("The redirect URL state did not match this login.")
        else:
            code = value
        if not code:
            raise WithingsError("No authorization code was found in the supplied value.")
        return code

    def exchange_code(self, code: str) -> None:
        self._require_app()
        response = self.session.post(
            TOKEN_URL,
            data={
                "action": "requesttoken",
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": self.redirect_uri,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        self._check_response(payload, "authorization code exchange")
        self._save_tokens(payload["body"])

    def _check_response(self, payload: dict[str, Any], action: str) -> None:
        if payload.get("status") != 0:
            raise WithingsError(
                f"Withings {action} failed (status {payload.get('status')}): "
                f"{payload.get('error') or payload.get('body') or 'unknown API error'}"
            )

    def _save_tokens(self, tokens: dict[str, Any]) -> None:
        try:
            access_token = tokens["access_token"]
            refresh_token = tokens["refresh_token"]
        except KeyError as exc:
            raise WithingsError("Withings token response omitted a required token.") from exc
        expires_in = int(tokens.get("expires_in", 10800))
        self.storage.write_json(
            TOKEN_FILE,
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "userid": tokens.get("userid"),
                "expires_at": int(datetime.now(timezone.utc).timestamp()) + expires_in,
            },
        )

    def _tokens(self) -> dict[str, Any]:
        return self.storage.read_json(TOKEN_FILE)

    def access_token(self) -> str:
        self._require_app()
        tokens = self._tokens()
        if not tokens.get("refresh_token"):
            raise WithingsError("Withings is not authenticated. Run: docker compose run --rm withings-sync auth withings")
        if int(tokens.get("expires_at", 0)) <= int(datetime.now(timezone.utc).timestamp()) + 300:
            response = self.session.post(
                TOKEN_URL,
                data={
                    "action": "requesttoken",
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": tokens["refresh_token"],
                },
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            self._check_response(payload, "token refresh")
            self._save_tokens(payload["body"])
            tokens = self._tokens()
        return str(tokens["access_token"])

    def get_measurements(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        token = self.access_token()
        groups: list[dict[str, Any]] = []
        offset = 0
        while True:
            response = self.session.post(
                MEASURE_URL,
                params={"action": "getmeas"},
                data={
                    "access_token": token,
                    "category": 1,
                    "startdate": int(start.timestamp()),
                    "enddate": int(end.timestamp()),
                    "offset": offset,
                },
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            self._check_response(payload, "measurement request")
            body = payload.get("body") or {}
            page = body.get("measuregrps") or []
            groups.extend(page)
            if not body.get("more") or not page:
                break
            next_offset = int(body.get("offset", offset + len(page)))
            if next_offset <= offset:
                raise WithingsError("Withings pagination did not advance its offset.")
            offset = next_offset
        return groups

    def get_height(self) -> float | None:
        token = self.access_token()
        response = self.session.post(
            MEASURE_URL,
            params={"action": "getmeas"},
            data={"access_token": token, "meastype": 4, "category": 1},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        self._check_response(payload, "height request")
        groups = (payload.get("body") or {}).get("measuregrps") or []
        heights = [
            (int(group.get("date", 0)), _scaled_value(measure))
            for group in groups
            for measure in group.get("measures", [])
            if int(measure.get("type", -1)) == 4 and _scaled_value(measure) is not None
        ]
        return max(heights, default=(0, None), key=lambda item: item[0])[1]

    def authenticate(self, value: str, expected_state: str | None = None) -> None:
        self.exchange_code(self.extract_code(value, expected_state))

    def status(self) -> tuple[bool, datetime | None]:
        tokens = self._tokens()
        if not tokens.get("refresh_token"):
            return False, None
        expiry = tokens.get("expires_at")
        return (
            True,
            datetime.fromtimestamp(int(expiry), timezone.utc) if expiry else None,
        )


def new_oauth_state() -> str:
    return uuid.uuid4().hex
