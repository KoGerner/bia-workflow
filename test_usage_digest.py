"""usage_digest — the weekly note KG reads: every user prompt verbatim (emails/phones masked),
tools called, last stage reached, drop-off (visibility layer C, 2026-08-16)."""
from __future__ import annotations

import json

import usage_digest as ud


def _week(tmp_path):
    calls = tmp_path / "calls-2026-W34.jsonl"
    calls.write_text(
        json.dumps({"ts": "2026-08-18T09:01:00+00:00", "tool": "start_journey", "journey_id": "run-bia",
                    "verdict": "ok"}) + "\n"
        + json.dumps({"ts": "2026-08-18T09:04:00+00:00", "tool": "write_company_file",
                      "path": "output/stage1-scope-and-guide.md", "verdict": "saved"}) + "\n"
        + json.dumps({"ts": "2026-08-18T14:00:00+00:00", "tool": "search", "query": "what is MTPD",
                      "verdict": "ok"}) + "\n")
    content = json.dumps({"activities": [
        {"type": "message", "from": {"role": "user"}, "text": "Start a BIA for marschkamp, mail me at kg@example.com",
         "timestamp": "2026-08-18T09:00:40Z", "channelId": "webchat"},
        {"type": "message", "from": {"role": "bot"}, "text": "Stage 1 · Identification of scope …",
         "timestamp": "2026-08-18T09:00:50Z"},
        {"type": "message", "from": {"role": "user"}, "text": "yes", "timestamp": "2026-08-18T09:03:30Z"},
        {"type": "message", "from": {"role": "user"}, "text": "call me +43 664 1234567",
         "timestamp": "2026-08-18T09:05:30Z"}]})
    (tmp_path / "transcripts-2026-W34.jsonl").write_text(json.dumps(
        {"conversationtranscriptid": "c1", "createdon": "2026-08-18T09:00:30Z", "content": content}) + "\n")


def test_digest_lists_every_user_prompt_and_masks_pii(tmp_path):
    _week(tmp_path)
    md = ud.digest(tmp_path, year=2026, week=34)
    assert md.startswith("---\ntype: resource")
    assert "- Start a BIA for marschkamp, mail me at [email]" in md
    assert "\n- yes\n" in md
    assert "call me [phone]" in md
    assert "kg@example.com" not in md and "664 1234567" not in md
    assert "start_journey" in md and "stage1-scope-and-guide.md" in md
    assert "last stage reached: Stage 1 · Identification of scope" in md
    assert "what is MTPD" in md  # top search queries in the roll-up


def test_digest_reads_the_sessions_csv_fallback(tmp_path):
    (tmp_path / "sessions-2026-08-20.csv").write_text(
        "SessionId,StartDateTime,Transcript\n"
        "s1,2026-08-20T10:00:00Z,\"User: hello there\nBot: hi\nUser: run a BIA\"\n")
    md = ud.digest(tmp_path, year=2026, week=34)
    assert "- hello there" in md and "- run a BIA" in md


def test_digest_with_empty_week_still_writes_a_note(tmp_path):
    md = ud.digest(tmp_path, year=2026, week=34)
    assert "## Conversations" in md and "none" in md.lower()


def test_digest_reads_the_real_copilot_studio_shape(tmp_path):
    """Measured 2026-08-16 on 79 real rows: from.role is 1 (user) / 0 (bot), timestamps are epoch
    seconds (+ timestampMs), and every MCP call is a planner event whose value.taskDialogId ends in
    ':<tool>' with `arguments`, followed by an event with `observation.structuredContent`."""
    acts = [
        {"type": "message", "from": {"role": 0}, "text": "Hello, I'm BIA-Workflow (public).", "timestamp": 1786514307, "channelId": "directline"},
        {"type": "message", "from": {"role": 1}, "text": "Start a BIA for marschkamp.", "timestamp": 1786514325, "timestampMs": 1786514325100, "channelId": "directline"},
        {"type": "event", "from": {"role": 0}, "timestamp": 1786514335, "value": {"taskDialogId": "MCP:x.action.AI-BCM:start_journey", "arguments": {}}},
        {"type": "event", "from": {"role": 0}, "timestamp": 1786514336, "value": {"taskDialogId": "MCP:x.action.AI-BCM:start_journey", "observation": {"isError": False, "structuredContent": {"stage_id": "scope-and-risk", "name": "Stage 1 · Identification of scope"}}}},
        {"type": "message", "from": {"role": 1}, "text": "yes", "timestamp": 1786514400},
        {"type": "event", "from": {"role": 0}, "timestamp": 1786514410, "value": {"taskDialogId": "MCP:x.action.AI-BCM:write_company_file", "arguments": {"company": "marschkamp", "path": "output/stage1-scope-and-guide.md"}}},
        {"type": "event", "from": {"role": 0}, "timestamp": 1786514412, "value": {"taskDialogId": "MCP:x.action.AI-BCM:write_company_file", "observation": {"isError": False, "structuredContent": {"written": True, "path": "marschkamp/output/stage1-scope-and-guide.md"}}}},
    ]
    (tmp_path / "transcripts-2026-W33.jsonl").write_text(json.dumps(
        {"conversationtranscriptid": "real1", "createdon": "2026-08-12T07:41:36Z", "content": json.dumps({"activities": acts})}) + "\n")
    md = ud.digest(tmp_path, year=2026, week=33)
    assert "- Start a BIA for marschkamp." in md and "\n- yes\n" in md
    assert "Hello, I'm BIA-Workflow" not in md  # bot lines are not prompts
    assert "tools: start_journey → write_company_file" in md
    assert "saved: output/stage1-scope-and-guide.md" in md
    assert "last stage reached: Stage 1 · Identification of scope" in md
    assert "directline" in md
