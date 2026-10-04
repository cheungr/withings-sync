import base64
from datetime import datetime, timezone
import json

import pytest

from withings_sync.garmin import GarminClient, token_expiry
from withings_sync.storage import Storage
from withings_sync.sync import SyncService


class FakeWithings:
    def __init__(self, groups):
        self.groups = groups
        self.calls = []

    def get_measurements(self, start, end):
        self.calls.append((start, end))
        return self.groups

    def get_height(self):
        return 1.75


class FakeGarmin:
    def __init__(self):
        self.uploads = []

    def upload(self, measurement):
        self.uploads.append(measurement.source_id)


def test_successful_upload_state_deduplicates_repeated_sync(tmp_path):
    storage = Storage(tmp_path)
    withings = FakeWithings(
        [
            {
                "grpid": 987,
                "date": 1786530600,
                "measures": [{"type": 1, "value": 71000, "unit": -3}],
            }
        ]
    )
    garmin = FakeGarmin()
    service = SyncService(storage, withings, garmin)

    assert service.run_once() == 1
    assert service.run_once() == 0
    state = storage.read_json("state.json")
    assert garmin.uploads == ["987"]
    assert state["uploaded_measurements"]["987"] == datetime.fromtimestamp(
        1786530600, timezone.utc
    ).isoformat()


def test_garmin_state_commits_only_after_upload_succeeds(tmp_path):
    storage = Storage(tmp_path)
    withings = FakeWithings(
        [
            {
                "grpid": 988,
                "date": 1786530600,
                "measures": [{"type": 1, "value": 71000, "unit": -3}],
            }
        ]
    )

    class FailedGarmin:
        def upload(self, measurement):
            raise RuntimeError("upload rejected")

    service = SyncService(storage, withings, FailedGarmin())
    with pytest.raises(RuntimeError, match="upload rejected"):
        service.run_once()

    assert storage.read_json("state.json") == {}


def test_garmin_status_reads_saved_tokens_and_expiry(tmp_path):
    storage = Storage(tmp_path)
    expiry = int(datetime.now(timezone.utc).timestamp()) + 600
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": expiry}).encode()
    ).decode().rstrip("=")
    token = f"header.{payload}.signature"
    storage.write_json(
        "garmin_tokens.json",
        {"di_token": token, "di_refresh_token": "refresh-token"},
    )

    connected, token_expires = GarminClient(storage).status()

    assert connected
    assert token_expires == datetime.fromtimestamp(expiry, timezone.utc)
    assert token_expiry(None) is None
