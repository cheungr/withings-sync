import json

from withings_sync.garmin import GarminClient
from withings_sync.storage import Storage


def test_garmin_mfa_prompt_saves_tokens_not_password(tmp_path, monkeypatch):
    storage = Storage(tmp_path)
    calls = {}

    class FakeTokenClient:
        def dump(self, path):
            with open(path, "w", encoding="utf-8") as token_file:
                json.dump({"di_token": "access", "di_refresh_token": "refresh"}, token_file)

    class FakeGarmin:
        def __init__(self, **kwargs):
            calls["kwargs"] = kwargs
            self.client = FakeTokenClient()

        def login(self, tokenstore):
            calls["mfa"] = calls["kwargs"]["prompt_mfa"]()
            calls["tokenstore"] = tokenstore

    monkeypatch.setattr("withings_sync.garmin.Garmin", FakeGarmin)
    client = GarminClient(storage)
    client.authenticate("user@example.com", "do-not-save-this", "123456")

    assert calls["mfa"] == "123456"
    assert calls["tokenstore"] == str(tmp_path / "garmin_tokens.json")
    assert "do-not-save-this" not in (tmp_path / "garmin_tokens.json").read_text()
    assert client.status()[0]
