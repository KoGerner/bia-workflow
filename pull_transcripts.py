"""Daily pull of Copilot Studio conversation transcripts (Dataverse) into JSONL on the VPS.

Visibility layer B (2026-08-16). The MCP server never sees the chat; the user's words are stored
by Microsoft in Dataverse (`conversationtranscript` table, default retention 30 days). This pulls
them, all bots in the environment, every night, into DIR/transcripts-YYYY-Www.jsonl
(0600) and advances last_pull.txt only after a successful write. usage_digest.py reads them.

Activity shape (Bot Framework, as stored by Copilot Studio; re-confirmed once the spike below
can read a row): `content` is a JSON string {"activities": [{"type": "message", "from": {"role":
"user"|"bot"}, "text": "...", "timestamp": "...", "channelId": "..."}]}. If the first real row
shows a different shape, replace this paragraph with what was observed — the digest keys on it.

Auth: refresh-token grant ONLY on the timer path, using the Entra app named in `dataverse.env`
(TENANT_ID/CLIENT_ID/DATAVERSE_ORG_URL — B1 2026-08-18: the file that was copilot-eval.env,
renamed, never deleted) and a refresh token seeded once into DIR/dataverse.token
({"refresh_token": "..."}, 0600). Seed it interactively with `--login` (device-code flow, B2 —
the one thing the deleted copilot_eval.py did for this lane); a headless timer never takes that
path. Spike 2026-08-16: the app lacked Dynamics CRM `user_impersonation` consent → AADSTS65001.
Until KG grants it (App registrations → AIBCM Copilot Evaluation Runner → API permissions →
Dynamics CRM → user_impersonation → admin consent) and sets DATAVERSE_ORG_URL, this exits 2
with the reason. Fallback while that is pending: Copilot Studio → Analytics → Download sessions
(CSV) into DIR as sessions-<date>.csv — usage_digest.py reads that shape too.

Run by hand:  set -a; . dataverse.env; set +a; .venv/bin/python pull_transcripts.py [--login]
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

DIR = Path(os.environ.get("BIA_WORKFLOW_USAGE_DIR",
                          Path(__file__).resolve().parent / "data" / "bia-usage"))
# Under the app root, not /opt/brain/data: the MCP unit runs ProtectSystem=strict with
# ReadWritePaths=/opt/brain/ai-addendum only — measured 2026-08-16, a row silently
# never landed. data/ is gitignored at any depth (.gitignore:8).
# Beside the usage dir, not in the checkout: /srv/addendum/dataverse.env on brain (svc-owned,
# 0600), <checkout>/data/dataverse.env locally — placed by the one USAGE_DIR knob (C13).
ENV_PATH = DIR.parent / "dataverse.env"
LOGIN = "https://login.microsoftonline.com"
SELECT = "conversationtranscriptid,createdon,content"  # measured 2026-08-16: no bot_* column on this entity
KEEP_DAYS = 180


def _env() -> dict:
    cfg = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    cfg.update({k: os.environ[k] for k in ("TENANT_ID", "CLIENT_ID", "DATAVERSE_ORG_URL") if k in os.environ})
    return cfg


def _token(http, org: str) -> str:
    cfg = _env()
    cache = DIR / "dataverse.token"
    if not cache.exists():
        raise SystemExit(f"no {cache}: seed it once with `pull_transcripts.py --login`")
    rt = json.loads(cache.read_text(encoding="utf-8")).get("refresh_token", "")
    r = http.post(f"{LOGIN}/{cfg['TENANT_ID']}/oauth2/v2.0/token",
                  data={"grant_type": "refresh_token", "refresh_token": rt,
                        "client_id": cfg["CLIENT_ID"], "scope": f"{org}/.default"})
    body = r.json()
    if r.status_code != 200:
        raise SystemExit(f"dataverse token refresh failed ({r.status_code} {body.get('error')}): "
                         f"{(body.get('error_description') or '')[:200]} — no device login from a timer; "
                         "if AADSTS65001, grant Dynamics CRM user_impersonation to the app in dataverse.env")
    if body.get("refresh_token"):
        cache.write_text(json.dumps({"refresh_token": body["refresh_token"]}), encoding="utf-8")
        os.chmod(cache, 0o600)
    return body["access_token"]


def login(http, sleep=time.sleep, clock=time.monotonic) -> None:
    """Interactive device-code sign-in (public client) that seeds DIR/dataverse.token — run
    once by a human; the timer path (`_token`) is refresh-only and never calls this."""
    cfg = _env()
    org = cfg["DATAVERSE_ORG_URL"]
    r = http.post(f"{LOGIN}/{cfg['TENANT_ID']}/oauth2/v2.0/devicecode",
                  data={"client_id": cfg["CLIENT_ID"], "scope": f"{org}/.default offline_access"})
    if r.status_code != 200:
        raise SystemExit(f"devicecode HTTP {r.status_code}")
    dc = r.json()
    print(dc.get("message")
          or f"Sign in: open {dc.get('verification_uri')} and enter code {dc.get('user_code')}")
    deadline = clock() + int(dc.get("expires_in", 900))
    while True:
        rt = http.post(f"{LOGIN}/{cfg['TENANT_ID']}/oauth2/v2.0/token",
                       data={"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                             "device_code": dc["device_code"], "client_id": cfg["CLIENT_ID"]})
        body = rt.json()
        if rt.status_code == 200:
            break
        if body.get("error") not in ("authorization_pending", "slow_down"):
            raise SystemExit(f"device login failed: {body.get('error') or rt.status_code}")
        if clock() >= deadline:
            raise SystemExit("device code expired before sign-in completed")
        sleep(int(dc.get("interval", 5)))
    DIR.mkdir(parents=True, exist_ok=True)
    cache = DIR / "dataverse.token"
    cache.write_text(json.dumps({"refresh_token": body["refresh_token"]}), encoding="utf-8")
    os.chmod(cache, 0o600)
    print(f"seeded {cache}")


def _get_pages(http, tok: str, url: str):
    while url:
        r = http.get(url, headers={"Authorization": f"Bearer {tok}", "Accept": "application/json",
                                   "Prefer": "odata.maxpagesize=100"})
        r.raise_for_status()
        j = r.json()
        yield j
        url = j.get("@odata.nextLink")


def pull(org: str, since: str) -> int:
    url = (f"{org}/api/data/v9.2/conversationtranscripts?$select={SELECT}"
           f"&$filter=createdon gt {since}&$orderby=createdon asc")
    rows: list[dict] = []
    with httpx.Client(timeout=60) as http:
        for page in _get_pages(http, _token(http, org), url):
            rows += page["value"]
    if rows:
        DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(DIR, 0o750)  # ACL mask: brain's read grant survives (see call_log._write)
        y, w, _ = datetime.now(timezone.utc).isocalendar()
        p = DIR / f"transcripts-{y}-W{w:02d}.jsonl"
        with p.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.chmod(p, 0o640)
        (DIR / "last_pull.txt").write_text(rows[-1]["createdon"], encoding="utf-8")
    return len(rows)


def prune(days: int = KEEP_DAYS) -> int:
    """Retention in the same run: raw JSONL older than `days` goes; digests live in the vault."""
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    gone = 0
    for old in DIR.glob("*.jsonl"):
        if old.stat().st_mtime < cutoff:
            old.unlink()
            gone += 1
    return gone


if __name__ == "__main__":
    org = _env().get("DATAVERSE_ORG_URL")
    if not org:
        sys.exit("DATAVERSE_ORG_URL not set (dataverse.env) — Power Platform admin center → environment → Environment URL")
    if "--login" in sys.argv[1:]:
        with httpx.Client(timeout=60) as http:
            login(http)
        sys.exit(0)
    state = DIR / "last_pull.txt"
    since = state.read_text(encoding="utf-8").strip() if state.exists() else "2026-08-01T00:00:00Z"
    n = pull(org, since)
    print(f"pulled {n} transcripts since {since}; pruned {prune()} old files")
