"""Command-line application and scheduled Withings-to-Garmin synchronization."""

from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta, timezone
import logging
import os
import re
import sys
import time as clock
from typing import Any

from withings_sync.garmin import GarminClient
from withings_sync.storage import Storage
from withings_sync.withings import (
    WithingsClient,
    new_oauth_state,
    parse_measurement_groups,
)

STATE_FILE = "state.json"
log = logging.getLogger("withings_sync")


def interval_seconds(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+)\s*([smhd]?)\s*", value.lower())
    if not match or int(match.group(1)) <= 0:
        raise argparse.ArgumentTypeError(
            "SYNC_INTERVAL must be a positive number of seconds or use s, m, h, or d."
        )
    factor = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
    return int(match.group(1)) * factor


def parse_since(value: str) -> datetime:
    try:
        return datetime.combine(
            datetime.strptime(value, "%Y-%m-%d").date(), time.min, timezone.utc
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD for --since.") from exc


class SyncService:
    def __init__(self, storage: Storage, withings: WithingsClient, garmin: GarminClient):
        self.storage = storage
        self.withings = withings
        self.garmin = garmin

    def run_once(self, since: datetime | None = None) -> int:
        state: dict[str, Any] = self.storage.read_json(STATE_FILE)
        last_synced = state.get("last_synced_at")
        if since is None:
            if last_synced:
                since = datetime.fromisoformat(last_synced) - timedelta(days=1)
            else:
                since = datetime(2009, 1, 1, tzinfo=timezone.utc)
        end = datetime.now(timezone.utc)
        groups = self.withings.get_measurements(since, end)
        height = self.withings.get_height()
        measurements = parse_measurement_groups(groups, height)
        uploaded = state.setdefault("uploaded_measurements", {})
        if not isinstance(uploaded, dict):
            raise ValueError("Invalid uploaded_measurements in sync state.")
        count = 0
        for measurement in measurements:
            if measurement.source_id in uploaded:
                continue
            self.garmin.upload(measurement)
            uploaded[measurement.source_id] = measurement.timestamp.isoformat()
            prior = state.get("last_synced_at")
            if not prior or measurement.timestamp > datetime.fromisoformat(prior):
                state["last_synced_at"] = measurement.timestamp.isoformat()
            self.storage.write_json(STATE_FILE, state)
            count += 1
            log.info(
                "Synced %s kg from %s",
                measurement.weight,
                measurement.timestamp.isoformat(),
            )
        log.info("Sync complete: %d new measurement(s).", count)
        return count

    def status(self, healthcheck: bool = False) -> int:
        withings_ok, withings_expiry = self.withings.status()
        garmin_ok, garmin_expiry = self.garmin.status()
        print(f"Withings: {'tokens saved' if withings_ok else 'not authenticated'}"
              f"{_expiry_text(withings_expiry)}")
        print(f"Garmin:   {'tokens saved' if garmin_ok else 'not authenticated'}"
              f"{_expiry_text(garmin_expiry)}")
        state = self.storage.read_json(STATE_FILE)
        print(f"Last sync: {state.get('last_synced_at', 'never')}")
        return 0 if (withings_ok and garmin_ok) else (1 if healthcheck else 0)


def _expiry_text(expiry: datetime | None) -> str:
    return f" (access token expires {expiry.isoformat()})" if expiry else " (expiry unknown)"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync Withings body composition to Garmin Connect.")
    subparsers = parser.add_subparsers(dest="command")
    auth = subparsers.add_parser("auth", help="Authenticate a service.")
    auth_commands = auth.add_subparsers(dest="service", required=True)
    auth_commands.add_parser("withings", help="Authorize a personal Withings app.")
    garmin_auth = auth_commands.add_parser("garmin", help="Authenticate Garmin Connect, including MFA.")
    garmin_auth.add_argument("--mfa-code", help="Supply an authenticator code without an interactive MFA prompt.")
    sync = subparsers.add_parser("sync", help="Run a synchronization.")
    sync.add_argument("--once", action="store_true", help="Perform one sync and exit.")
    sync.add_argument("--since", type=parse_since, help="Backfill from YYYY-MM-DD.")
    subparsers.add_parser("status", help="Show saved token and sync status.")
    subparsers.choices["status"].add_argument("--healthcheck", action="store_true")
    return parser


def _new_service(storage: Storage) -> SyncService:
    return SyncService(storage, WithingsClient(storage), GarminClient(storage))


def _authenticate_withings(client: WithingsClient) -> None:
    state = new_oauth_state()
    print("Open this URL and authorize access to your Withings measurements:")
    print(client.authorization_url(state))
    print("Paste the full redirect URL or its authorization code.")
    value = input("Redirect URL or code: ")
    client.authenticate(value, state)
    log.info("Withings authorization saved.")


def _scheduler(service: SyncService, storage: Storage, interval: int) -> None:
    log.info("Starting sync scheduler (interval: %s seconds).", interval)
    while True:
        try:
            with storage.lock():
                service.run_once()
        except Exception:
            log.exception("Scheduled sync failed; will retry after the next interval.")
        clock.sleep(interval)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args()
    storage = Storage(os.getenv("DATA_DIR", "./data"))
    try:
        if args.command == "auth" and args.service == "withings":
            with storage.lock():
                _authenticate_withings(WithingsClient(storage))
        elif args.command == "auth" and args.service == "garmin":
            with storage.lock():
                GarminClient(storage).authenticate_interactively(args.mfa_code)
            log.info("Garmin authentication saved.")
        elif args.command == "sync":
            service = _new_service(storage)
            if args.once or args.since:
                with storage.lock():
                    service.run_once(args.since)
            else:
                _scheduler(service, storage, interval_seconds(os.getenv("SYNC_INTERVAL", "1h")))
        elif args.command == "status":
            if args.healthcheck:
                sys.exit(_new_service(storage).status(args.healthcheck))
            with storage.lock():
                sys.exit(_new_service(storage).status())
        else:
            _scheduler(_new_service(storage), storage, interval_seconds(os.getenv("SYNC_INTERVAL", "1h")))
    except KeyboardInterrupt:
        log.info("Stopped.")
    except Exception as exc:
        log.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
