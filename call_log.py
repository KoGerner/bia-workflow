"""One JSON line per MCP tool call — names, ids, sizes, verdicts. Never file content.

Visibility layer A (2026-08-16). The MCP server sees tool calls, not the chat; the user's
words live in Copilot Studio (pulled by pull_transcripts.py). What this log answers: which
stages users reach, where next_step refuses, what they search for, how often the referee
rejects and why, what gets saved. It cannot name a user — one bearer for every Copilot user,
stateless HTTP — grouping is by time (usage_digest.py joins on it).

Rows go to <app root>/data/bia-usage/calls-YYYY-Www.jsonl (0600; data/ is gitignored).
"""
from __future__ import annotations

import functools
import inspect
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(os.environ.get("BIA_WORKFLOW_USAGE_DIR",
                          Path(__file__).resolve().parent / "data" / "bia-usage"))
# Under the app root, not /opt/brain/data: the MCP unit runs ProtectSystem=strict with
# ReadWritePaths=/opt/brain/ai-addendum only — measured 2026-08-16, a row silently
# never landed. data/ is gitignored at any depth (.gitignore:8).
# Argument names worth keeping verbatim (ids and short user intent), never bodies.
KEEP = ("journey_id", "stage_id", "company", "path", "query", "q", "asset_id", "topic",
        "activity_id", "task", "output_type", "term")
# Bodies: log their size only.
SIZED = ("content", "record", "changes", "record_json", "fields")


def _summary(kw: dict) -> dict:
    out = {k: v for k, v in kw.items() if k in KEEP and isinstance(v, (str, int))}
    for k in SIZED:
        v = kw.get(k)
        if v is not None:
            # Leo's audit 2026-08-19: `len(str)` counts CHARACTERS. The marschkamp files are
            # German, so every non-ASCII body logged fewer "bytes" than it had — six of seven
            # numbers in run (b)'s slice disagreed with the folder, and the verification read the
            # disagreement as the report being wrong. A byte field that is not bytes corroborates
            # nothing; encode, then count.
            text = v if isinstance(v, str) else json.dumps(v, default=str)
            out[f"{k}_bytes"] = len(text.encode("utf-8"))
    return out


def _verdict(res) -> str:
    # server.py wraps payloads in CallToolResult; unwrap so verdicts see the dict.
    structured = getattr(res, "structuredContent", None)
    if isinstance(structured, dict):
        res = structured
    if isinstance(res, dict):
        if "error" in res:
            return f"error:{res['error']}"
        if "pass" in res:
            return "PASS" if res["pass"] else "FAIL"
        if res.get("verification"):
            return "saved"
    return "ok"


def _write(row: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    # 0750 / 0640, not 0700 / 0600: chmod rewrites the POSIX-ACL mask (the group bits), and the
    # read grant for `brain` on the usage dir (setfacl u:brain:r, 2026-08-18) died on the first
    # logged call under 0600. Group svc-bia has no members, so without an ACL nothing changes.
    os.chmod(DIR, 0o750)
    y, w, _ = datetime.now(timezone.utc).isocalendar()
    p = DIR / f"calls-{y}-W{w:02d}.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.chmod(p, 0o640)


def _bytes_written(res) -> int | None:
    """What the tool says it wrote. The record lane binds its bytes server-side by save token,
    so the content argument is empty and its length is a lie about the save (run (b) 2026-08-18:
    output/bia-record.json logged 0 bytes on every run)."""
    structured = getattr(res, "structuredContent", None)
    if isinstance(structured, dict):
        res = structured
    size = res.get("size") if isinstance(res, dict) else None
    return size if isinstance(size, int) else None


def _conversation(kw: dict) -> str | None:
    """A short, stable, opaque id for the conversation a call belongs to.

    Until 2026-08-20 a row said what happened and never who, so a Copilot run could only be
    attributed by wall-clock window — which is also why two surfaces sharing this server could
    not be told apart, and why the read store keyed by company cannot separate two testers.
    `ctx.client_id` is what the client calls itself; the session object identifies the
    connection when it does not.

    Hashed because this file is read by people debugging and the value comes from the client:
    it should identify a conversation, not publish whatever the client chose to call itself.
    ponytail: 8 hex chars — collision-irrelevant for eyeballing a day of calls, short enough
    that the log still reads in a terminal.
    """
    ctx = kw.get("ctx")
    if ctx is None:
        return None
    source = getattr(ctx, "client_id", None) or id(getattr(ctx, "session", None) or ctx)
    return hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:8]


def _record(fn, kw, res, t0) -> None:
    try:
        row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tool": fn.__name__,
               "ms": int((time.time() - t0) * 1000), **_summary(kw), "verdict": _verdict(res)}
        conv = _conversation(kw)
        if conv:
            row["conv"] = conv
        written = _bytes_written(res)
        if written is not None:
            row["content_bytes"] = written
        _write(row)
    except Exception:  # ponytail: a log line must never fail a tool call
        pass


def logged(fn):
    """Wrap an @mcp.tool function; FastMCP calls tools with kwargs, and functools.wraps keeps
    the signature it reads for the tool schema. Sync only: every registered tool is a plain
    def (asserted by test_every_logged_tool_is_sync), so the async branch this carried until
    2026-08-24 had never wrapped anything."""
    @functools.wraps(fn)
    def w(*a, **kw):
        t0 = time.time()
        res = fn(*a, **kw)
        _record(fn, kw, res, t0)
        return res
    return w
