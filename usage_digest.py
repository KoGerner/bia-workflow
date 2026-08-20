"""Weekly usage digest of the BIA-Workflow — what people actually typed (visibility layer C).

Joins layer A (call_log.py rows: tool, stage, query, path, verdict) with layer B
(pull_transcripts.py rows: Copilot Studio conversation transcripts) by time, and writes ONE
vault note per ISO week:

    02-Projects/BCI/ai-addendum/usage/bia-usage-digest-<year>-w<ww>.md

Per conversation: start time, surface, EVERY user prompt verbatim (emails and phone numbers
masked — nothing else is altered; the point is to read what people type), tools called in
order, saved documents, referee rejections, last stage reached, drop-off. Then a roll-up:
prompts by stage, top search queries, drop-off histogram, prompts with no tool call after them.

Reads both raw shapes: transcripts-*.jsonl (Dataverse `content` = activities; from.role 1=user/0=bot,
epoch timestamps, tool calls as planner events — measured 2026-08-16) and
the fallback sessions-*.csv (Copilot Studio Analytics → Download sessions; a `Transcript` column
with "User: …" / "Bot: …" lines).

Run:  .venv/bin/python usage_digest.py [YEAR WEEK]   (defaults to last ISO week)
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

DIR = Path(os.environ.get("BIA_WORKFLOW_USAGE_DIR",
                          Path(__file__).resolve().parent / "data" / "bia-usage"))
# Under the app root, not /opt/brain/data: the MCP unit runs ProtectSystem=strict with
# ReadWritePaths=/opt/brain/ai-addendum only — measured 2026-08-16, a row silently
# never landed. data/ is gitignored at any depth (.gitignore:8).
VAULT_OUT = Path(os.environ.get("BRAIN_VAULT_PATH", "/opt/brain-live")) / "02-Projects/BCI/ai-addendum/usage"
JOIN_SLACK = timedelta(minutes=5)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d ()/-]{7,}\d")


def mask(text: str) -> str:
    return _PHONE.sub("[phone]", _EMAIL.sub("[email]", text or ""))


def _ts(s) -> datetime | None:
    """ISO string, or epoch seconds/milliseconds (Copilot Studio transcripts use `timestamp` =
    epoch seconds and `timestampMs`, measured 2026-08-16)."""
    if s in (None, ""):
        return None
    if isinstance(s, (int, float)):
        return datetime.fromtimestamp(s / 1000 if s > 1e11 else s, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _is_user(a: dict) -> bool:
    # Copilot Studio stores from.role as 1 (user) / 0 (bot); Bot Framework exports use "user"/"bot".
    return (a.get("from") or {}).get("role") in (1, "1", "user")


def _tool_steps(acts: list[dict]) -> list[dict]:
    """[{tool, args, ok, stage_id, written, path, pass}] from the planner events — the transcript
    records every MCP call as an event whose value.taskDialogId ends in ':<tool>' with `arguments`,
    followed by an event carrying `observation.structuredContent`."""
    steps: list[dict] = []
    for a in acts:
        v = a.get("value")
        if not isinstance(v, dict) or not isinstance(v.get("taskDialogId"), str) or ":" not in v["taskDialogId"]:
            continue
        tool = v["taskDialogId"].rsplit(":", 1)[-1]
        if "arguments" in v:
            steps.append({"tool": tool, "args": v.get("arguments") or {}, "ts": _ts(a.get("timestampMs") or a.get("timestamp"))})
        elif "observation" in v and steps and steps[-1]["tool"] == tool and "obs" not in steps[-1]:
            obs = v.get("observation") or {}
            sc = obs.get("structuredContent") or {}
            steps[-1].update({"obs": True, "error": bool(obs.get("isError")) or "error" in sc,
                              "stage_id": sc.get("stage_id"), "written": bool(sc.get("written")),
                              "pass": sc.get("pass")})
    return steps


def _stage_names() -> dict[str, str]:
    try:
        import journeys as journey_engine
        return {s.id: s.name or s.id for s in journey_engine.load_journeys()["run-bia"].stages}
    except Exception:  # digest must still render without the journey loaded
        return {}


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def _conversations(dirpath: Path) -> list[dict]:
    """[{id, start, surface, turns:[(ts, text)]}] from transcripts-*.jsonl and sessions-*.csv."""
    convs = []
    for p in sorted(dirpath.glob("transcripts-*.jsonl")):
        for row in _read_jsonl(p):
            try:
                content = json.loads(row.get("content") or "{}")
            except ValueError:
                content = {}
            acts = content.get("activities") or []
            turns = [(_ts(a.get("timestampMs") or a.get("timestamp")), a.get("text") or "") for a in acts
                     if a.get("type") == "message" and _is_user(a) and a.get("text")]
            surface = next((a.get("channelId") for a in acts if a.get("channelId")), "unknown")
            convs.append({"id": row.get("conversationtranscriptid", "?"), "start": _ts(row.get("createdon", "")),
                          "surface": surface, "turns": turns, "steps": _tool_steps(acts)})
    for p in sorted(dirpath.glob("sessions-*.csv")):
        with p.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                text = r.get("Transcript") or r.get("transcript") or ""
                turns = [(None, m.strip()) for m in re.findall(r"(?im)^User:\s*(.+)$", text)]
                convs.append({"id": r.get("SessionId") or r.get("sessionId") or "?",
                              "start": _ts(r.get("StartDateTime") or r.get("startDateTime") or ""),
                              "surface": "sessions-csv", "turns": turns, "steps": []})
    return convs


def _calls_between(calls: list[dict], start, end) -> list[dict]:
    if start is None:
        return []
    end = (end or start) + JOIN_SLACK
    out = []
    for c in calls:
        t = _ts(c.get("ts", ""))
        if t and start - JOIN_SLACK <= t <= end:
            out.append(c)
    return out


def digest(dirpath: Path, year: int, week: int) -> str:
    names = _stage_names()
    order = list(names)
    calls_path = dirpath / f"calls-{year}-W{week:02d}.jsonl"
    calls = _read_jsonl(calls_path) if calls_path.exists() else []
    convs = _conversations(dirpath)
    today = datetime.now(timezone.utc).date().isoformat()
    out = ["---", "type: resource", f"created: {today}", f"updated: {today}", "status: active",
           "tags: [bia-workflow, usage, digest]", "source: brain", "generator: usage_digest.py", "---",
           f"# BIA-Workflow usage digest — {year} W{week:02d}", "",
           "What users typed into the BIA-Workflow this week (verbatim, emails/phones masked), which tools "
           "the agent called, where each conversation stopped. Raw data stays on the VPS "
           "(`<app root>/data/bia-usage/`, 180 days).", "", "## Conversations", ""]
    drop = Counter()
    prompts_by_stage: dict[str, list[str]] = {}
    unanswered = []
    if not convs:
        out.append("_none recorded this week (no transcripts pulled — check the pull timer / consent, or the sessions CSV)_")
    for c in convs:
        turns = c["turns"]
        first = turns[0][0] if turns and turns[0][0] else c["start"]
        last = turns[-1][0] if turns and turns[-1][0] else c["start"]
        steps = c.get("steps") or []
        if steps:  # the transcript itself records every tool call — no time join needed
            cc = [{"tool": s["tool"], "stage_id": s.get("stage_id") or (s["args"] or {}).get("stage_id"),
                   "path": (s["args"] or {}).get("path"),
                   "verdict": ("error" if s.get("error") else "saved" if s.get("written")
                               else "PASS" if s.get("pass") is True else "FAIL" if s.get("pass") is False else "ok")}
                  for s in steps]
        else:
            cc = _calls_between(calls, first, last)
        stage_ids = [x.get("stage_id") for x in cc if x.get("tool") in ("start_journey", "next_step") and x.get("stage_id")]
        if any(x.get("tool") == "start_journey" for x in cc) and not stage_ids and order:
            stage_ids = [order[0]]
        last_stage = max(stage_ids, key=lambda s: order.index(s) if s in order else -1) if stage_ids else None
        last_name = names.get(last_stage, last_stage) if last_stage else "— (no journey call)"
        saved = [x.get("path") for x in cc if x.get("verdict") == "saved" and x.get("path")]
        rejections = [x for x in cc if x.get("verdict") == "FAIL"]
        advanced = any(x.get("tool") == "next_step" and x.get("verdict") == "ok" for x in cc)
        if last_stage and not advanced:
            drop[last_name] += 1
        for _, t in turns:
            prompts_by_stage.setdefault(last_name, []).append(mask(t))
        out.append(f"### {c['start'].isoformat(timespec='minutes') if c['start'] else '?'} · {c['surface']} · `{c['id']}`")
        out.append(f"- last stage reached: {last_name}")
        out.append(f"- tools: {' → '.join(x.get('tool', '?') for x in cc) or 'none'}")
        out.append(f"- saved: {', '.join(saved) or 'nothing'}" + (f" · referee FAIL ×{len(rejections)}" if rejections else ""))
        out.append("- prompts:")
        for _, t in turns:
            out.append(f"- {mask(t)}")
        if turns and not cc:
            unanswered.append(mask(turns[-1][1]))
        out.append("")
    out += ["## Roll-up", ""]
    out.append(f"- conversations: {len(convs)} · tool calls this week: {len(calls)}")
    out.append("- drop-off by last stage: " + (", ".join(f"{k}: {v}" for k, v in drop.most_common()) or "none"))
    q = Counter(x.get("query") or x.get("q") for x in calls if x.get("tool") == "search" and (x.get("query") or x.get("q")))
    out.append("- top search queries: " + (", ".join(f"“{mask(k)}” ×{v}" for k, v in q.most_common(10)) or "none"))
    v = Counter(x.get("verdict", "ok") for x in calls)
    out.append("- verdicts: " + (", ".join(f"{k} ×{n}" for k, n in v.most_common()) or "none"))
    out.append("- prompts with no tool call after them: " + (" | ".join(unanswered) or "none"))
    out.append("")
    out.append("### Prompts by stage")
    for stage, ps in prompts_by_stage.items():
        out.append(f"- **{stage}** ({len(ps)}): " + " | ".join(ps[:12]))
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) >= 3:
        year, week = int(argv[1]), int(argv[2])
    else:
        year, week, _ = (datetime.now(timezone.utc) - timedelta(days=7)).isocalendar()
    md = digest(DIR, year, week)
    VAULT_OUT.mkdir(parents=True, exist_ok=True)
    dest = VAULT_OUT / f"bia-usage-digest-{year}-w{week:02d}.md"  # vault naming rule: plain lowercase-hyphen slug
    if dest.exists():  # BRAIN_SPEC: never reset `created` on an existing note
        m = re.search(r"^created: (\S+)$", dest.read_text(encoding="utf-8"), re.M)
        if m:
            md = re.sub(r"^created: \S+$", f"created: {m.group(1)}", md, count=1, flags=re.M)
    dest.write_text(md, encoding="utf-8")
    _write_index(VAULT_OUT)
    print(dest)
    return 0


def _write_index(folder: Path) -> None:
    """usage/index.md links every digest so no weekly note is an orphan (vault linking rule)."""
    idx = folder / "index.md"
    created = datetime.now(timezone.utc).date().isoformat()
    if idx.exists():
        m = re.search(r"^created: (\S+)$", idx.read_text(encoding="utf-8"), re.M)
        if m:
            created = m.group(1)
    notes = sorted(p.stem for p in folder.glob("bia-usage-digest-*.md"))
    idx.write_text("\n".join([
        "---", "type: resource", f"created: {created}",
        f"updated: {datetime.now(timezone.utc).date().isoformat()}", "status: active",
        "tags: [bia-workflow, usage, digest]", "source: brain", "generator: usage_digest.py", "---",
        "# BIA-Workflow usage digests", "",
        "One note per ISO week, written by `usage_digest.py` in the bia-workflow checkout on brain "
        "from the pulled Copilot Studio transcripts and the tool call log. Newest last.", "",
        *[f"- [[{n}]]" for n in notes], ""]), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
