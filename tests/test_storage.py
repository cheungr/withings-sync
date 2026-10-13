import json
import os
from datetime import datetime, timezone

import pytest

from withings_sync.storage import Storage
from withings_sync.withings import TOKEN_FILE, WithingsClient, WithingsError


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return next(self.responses)


def test_json_state_is_atomic_private_and_readable(tmp_path):
    storage = Storage(tmp_path)
    storage.write_json("credentials.json", {"refresh_token": "secret"})

    assert storage.read_json("credentials.json") == {"refresh_token": "secret"}
    assert os.stat(tmp_path / "credentials.json").st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.tmp"))


def test_withings_refresh_rotates_and_persists_token_immediately(tmp_path):
    storage = Storage(tmp_path)
    storage.write_json(
        TOKEN_FILE,
        {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": int(datetime.now(timezone.utc).timestamp()) - 1,
        },
    )
    session = Session(
        [
            Response(
                {
                    "status": 0,
                    "body": {
                        "access_token": "new-access",
                        "refresh_token": "new-refresh",
                        "userid": 7,
                        "expires_in": 3600,
                    },
                }
            )
        ]
    )
    client = WithingsClient(storage, "app-id", "app-secret", "https://callback", session)

    assert client.access_token() == "new-access"
    saved = storage.read_json(TOKEN_FILE)
    assert saved["refresh_token"] == "new-refresh"
    assert saved["expires_at"] > int(datetime.now(timezone.utc).timestamp())
    assert session.calls[0][1]["data"]["refresh_token"] == "old-refresh"


def test_failed_refresh_does_not_replace_rotating_refresh_token(tmp_path):
    storage = Storage(tmp_path)
    original = {"access_token": "old", "refresh_token": "still-needed", "expires_at": 0}
    storage.write_json(TOKEN_FILE, original)
    session = Session([Response({"status": 401, "error": "invalid refresh token"})])
    client = WithingsClient(storage, "id", "secret", "redirect", session)

    with pytest.raises(WithingsError, match="refresh"):
        client.access_token()
    assert storage.read_json(TOKEN_FILE)["refresh_token"] == "still-needed"
    assert json.loads((tmp_path / TOKEN_FILE).read_text()) == original
