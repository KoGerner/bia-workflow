"""pull_transcripts — daily Copilot Studio conversation transcripts (Dataverse) → JSONL on the VPS
(visibility layer B, 2026-08-16). Network is mocked; the spike on 2026-08-16 answered the live
questions (refresh token alive; Dynamics CRM consent missing → AADSTS65001 until KG grants it)."""
from __future__ import annotations

import json

import pytest

import pull_transcripts as pt


def test_pull_appends_rows_and_advances_watermark(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "DIR", tmp_path)
    pages = [{"value": [{"conversationtranscriptid": "a", "createdon": "2026-08-17T01:00:00Z", "content": "{}"}],
              "@odata.nextLink": "next"},
             {"value": [{"conversationtranscriptid": "b", "createdon": "2026-08-17T02:00:00Z", "content": "{}"}]}]
    monkeypatch.setattr(pt, "_get_pages", lambda http, tok, url: iter(pages))
    monkeypatch.setattr(pt, "_token", lambda http, org: "t")
    n = pt.pull(org="https://x.crm4.dynamics.com", since="2026-08-16T00:00:00Z")
    assert n == 2
    rows = [json.loads(l) for p in tmp_path.glob("transcripts-*.jsonl") for l in p.read_text().splitlines()]
    assert [r["conversationtranscriptid"] for r in rows] == ["a", "b"]
    assert (tmp_path / "last_pull.txt").read_text() == "2026-08-17T02:00:00Z"


def test_pull_with_nothing_new_leaves_watermark_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "DIR", tmp_path)
    monkeypatch.setattr(pt, "_get_pages", lambda http, tok, url: iter([{"value": []}]))
    monkeypatch.setattr(pt, "_token", lambda http, org: "t")
    assert pt.pull(org="https://x.crm4.dynamics.com", since="2026-08-16T00:00:00Z") == 0
    assert not (tmp_path / "last_pull.txt").exists()
    assert not list(tmp_path.glob("transcripts-*.jsonl"))


def test_token_is_refresh_only_and_names_the_consent_gap(tmp_path, monkeypatch):
    """A headless timer must never fall into the interactive device-code login; a missing
    consent (AADSTS65001) is reported, not retried."""
    monkeypatch.setattr(pt, "DIR", tmp_path)
    (tmp_path / "dataverse.token").write_text(json.dumps({"refresh_token": "r"}))
    monkeypatch.setattr(pt, "_env", lambda: {"TENANT_ID": "t", "CLIENT_ID": "c"})

    class Resp:
        status_code = 400
        def json(self):
            return {"error": "invalid_grant", "error_description": "AADSTS65001: not consented"}

    class Http:
        def post(self, url, data):
            assert data["grant_type"] == "refresh_token" and data["scope"].endswith("/.default")
            return Resp()

    with pytest.raises(SystemExit) as e:
        pt._token(Http(), "https://x.crm4.dynamics.com")
    assert "AADSTS65001" in str(e.value)


def test_login_seeds_the_refresh_token_once(tmp_path, monkeypatch, capsys):
    """B2 (2026-08-18): the one-off device-code login lives here as `--login` — the only thing
    the deleted copilot_eval.py did for this lane was seed dataverse.token. It is interactive
    by design and never runs from the timer path (`_token` stays refresh-only)."""
    monkeypatch.setattr(pt, "DIR", tmp_path)
    monkeypatch.setattr(pt, "_env", lambda: {"TENANT_ID": "t", "CLIENT_ID": "c",
                                            "DATAVERSE_ORG_URL": "https://x.crm4.dynamics.com"})
    calls = []

    class Resp:
        def __init__(self, code, body):
            self.status_code, self._body = code, body
        def json(self):
            return self._body

    class Http:
        def post(self, url, data):
            calls.append((url.rsplit("/", 1)[1], data))
            if url.endswith("/devicecode"):
                assert data["scope"] == "https://x.crm4.dynamics.com/.default offline_access"
                return Resp(200, {"device_code": "dc", "user_code": "ABCD", "interval": 0,
                                  "verification_uri": "https://microsoft.com/devicelogin",
                                  "message": "go sign in"})
            if len([c for c in calls if c[0] == "token"]) == 1:
                return Resp(400, {"error": "authorization_pending"})
            return Resp(200, {"access_token": "a", "refresh_token": "r-new", "expires_in": 3600})

    pt.login(Http(), sleep=lambda s: None)
    tok = tmp_path / "dataverse.token"
    assert json.loads(tok.read_text()) == {"refresh_token": "r-new"}
    assert oct(tok.stat().st_mode & 0o777) == "0o600"
    assert "go sign in" in capsys.readouterr().out
    assert [c[0] for c in calls] == ["devicecode", "token", "token"]
