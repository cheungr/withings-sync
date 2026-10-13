"""Garmin Connect authentication and FIT body-composition uploads."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import getpass
import json
import logging
from typing import Callable

from garminconnect import Garmin
from garminconnect.exceptions import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
)

from withings_sync.fit import FitEncoderWeight
from withings_sync.storage import Storage
from withings_sync.withings import Measurement

log = logging.getLogger(__name__)
GARMIN_TOKEN_FILE = "garmin_tokens.json"
REAUTH_MESSAGE = (
    "Garmin authentication failed. Run: "
    "docker compose run --rm withings-sync auth garmin"
)


def token_expiry(token: str | None) -> datetime | None:
    if not token:
        return None
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return datetime.fromtimestamp(int(claims["exp"]), timezone.utc)
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


class GarminClient:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.token_path = storage.directory / GARMIN_TOKEN_FILE
        self.client: Garmin | None = None

    def status(self) -> tuple[bool, datetime | None]:
        try:
            data = json.loads(self.token_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False, None
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Cannot read Garmin token file: {exc}") from exc
        expiry = token_expiry(data.get("di_token"))
        return bool(data.get("di_refresh_token") or data.get("di_token")), expiry

    def _login(
        self,
        email: str | None = None,
        password: str | None = None,
        prompt_mfa: Callable[[], str] | None = None,
    ) -> Garmin:
        self.token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        client = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
        client.login(tokenstore=str(self.token_path))
        self.client = client
        self._persist()
        return client

    def connect(self) -> Garmin:
        if self.client:
            return self.client
        try:
            return self._login()
        except GarminConnectAuthenticationError as exc:
            raise RuntimeError(f"{REAUTH_MESSAGE}. Details: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"Garmin Connect is unavailable: {exc}") from exc

    def authenticate(
        self,
        email: str,
        password: str,
        mfa_code: str | None = None,
    ) -> None:
        def prompt() -> str:
            return mfa_code if mfa_code else input("Garmin MFA code: ").strip()

        self._login(email=email, password=password, prompt_mfa=prompt)
        self._persist()

    def authenticate_interactively(self, mfa_code: str | None = None) -> None:
        has_tokens, expiry = self.status()
        if has_tokens:
            try:
                self._login()
                log.info(
                    "Existing Garmin token is valid; no password is needed%s.",
                    f" (access expires {expiry.isoformat()})" if expiry else "",
                )
                return
            except GarminConnectAuthenticationError:
                log.info("Saved Garmin credentials were rejected; a new login is required.")
            except GarminConnectConnectionError as exc:
                raise RuntimeError(f"Garmin login could not reach Connect: {exc}") from exc
        email = input("Garmin email: ").strip()
        password = getpass.getpass("Garmin password: ")
        self.authenticate(email, password, mfa_code)

    def _persist(self) -> None:
        if not self.client:
            raise RuntimeError("Garmin client has no authenticated session.")
        self.client.client.dump(str(self.token_path))

    def upload(self, measurement: Measurement) -> None:
        client = self.connect()
        encoder = FitEncoderWeight()
        encoder.write_file_info(time_created=measurement.timestamp)
        encoder.write_file_creator()
        encoder.write_device_info(timestamp=measurement.timestamp)
        encoder.write_weight_scale(
            timestamp=measurement.timestamp,
            weight=measurement.weight,
            percent_fat=measurement.percent_fat,
            percent_hydration=measurement.percent_hydration,
            bone_mass=measurement.bone_mass,
            muscle_mass=measurement.muscle_mass,
            visceral_fat_rating=measurement.visceral_fat,
            bmi=measurement.bmi,
        )
        encoder.finish()
        try:
            client.client.post(
                "connectapi",
                "/upload-service/upload",
                files={"file": ("body_composition.fit", encoder.getvalue())},
                api=True,
            )
        except GarminConnectAuthenticationError as exc:
            raise RuntimeError(f"{REAUTH_MESSAGE}. Details: {exc}") from exc
        finally:
            self._persist()
