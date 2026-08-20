"""voice_check.py — grades how Bruno's turns read: whether each one ends open, and then length,
stage cards, narration, bad echoes.

`open_endings` is the one check here that asserts the agent DID something; the other eight are
prohibitions, so before it a set of short, unbannered, empty turns scored PASS.

Turns are `list[str]`, one agent message each. Exactly one input mode per run:

  --relay <channel-uuid> [--since <unix-ts>] [--author <hex-pubkey>]
      Shells `buzz messages get --channel <channel> [--since <ts>] --limit 500` (buzz reads
      BUZZ_PRIVATE_KEY / BUZZ_RELAY_URL from the environment) and keeps Bruno's turns.
      e.g. python3 voice_check.py --relay 0123-...-uuid --expect-card yes

  --transcripts [<dir>]
      Reads transcripts-*.jsonl (Dataverse conversationtranscript rows) — grades the Teams
      agent, no relay involved. Default: $BIA_WORKFLOW_DATA_DIR/bia-usage, else ./data/bia-usage.
      e.g. python3 voice_check.py --transcripts --max-median 700

  --file <path>
      Turns split on a line that is exactly `----`.
      e.g. python3 voice_check.py --file thread1.txt --expect-card no --max-chars 700

  --payload
      No turns: prints the run-bia stage-payload budget table and exits.
      e.g. python3 voice_check.py --payload

Prints one `name: value` line per counter (greppable), then `verdict: PASS|FAIL` and any
`fail: <reason>` lines. Exit 0 pass, 1 fail, 2 no turns (also a relay error, or no mode given).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
from pathlib import Path

import journeys
import usage_digest as ud

BRUNO_PUBKEY = "0e9fe7cb2c90c7c11be180c46bada0b70cd88c7c0cb95948c0fa41914361d702"
CARD_RE = re.compile(r"^[*_#\s]*Stage \d+a? (of \d+ )?· ")
LINK_RE = re.compile(r"\[[^\]]*\]\(http[^)]*\)")
# An inline numbered choice, in the shapes the retired `Next:` list was replaced by on
# 2026-08-19: "(1)", "reply 1", "choose 2", "2 or 3". Measured against both graded runs that
# day — 3 of 3 stage turns, 0 of 3 bare turns.
# `1 yes, 2 amend` is Hans's ruling the same evening, asked as the manager who answers these:
# numbers always, but the word stays next to the number — one key to press, and a thread that
# still reads next week instead of a column of bare 1s.
UNIT = r"(?:h|hr|hrs|hour|hours|d|day|days|w|week|weeks|m|min|mins|minute|minutes|mo|month|months|s|sec|secs)"
# The unit lookahead is not decoration: without it "2 h, 8 h" and "RTO 1 h and MTPD 2 days"
# read as numbered choices, and an ordinary impact sentence scores as an open ending.
OPEN_RE = re.compile(r"\(\d\)|\breply \d|\bchoose \d|\b\d or \d", re.I)
# One numbered option, wherever it sits. Four false FAILs in one evening came from encoding the
# RENDERING of a menu — "(1)", then "1 yes, 2 amend", then options on their own lines, then
# "1 yes = … . 2 amend = …" split by a full stop. This encodes the PROPERTY instead and says
# nothing about the punctuation between options.
# The separator between a number and its label is rendering, not meaning: `1 yes`, `1 — yes`
# and `1. Keep` are one menu in three coats, and the first two arrived on the same live run
# (2026-08-20). Encoding only the space cost a false FAIL on a turn that closed correctly.
# The digit must still be followed by whitespace or a separator, or "12 sites" and "2026"
# would read as options; the unit lookahead still keeps "2 h, 8 h" out.
OPTION_RE = re.compile(rf"\b(\d)(?:\s+(?:[—–.):-]\s*)?|[—–.):-]\s+)(?!{UNIT}\b)\w", re.I)
NARRATION_OPENERS = ("i'm applying", "i am applying", "i'm retrieving", "i am retrieving",
                     "i'll now", "i will now", "let me check", "let me retrieve",
                     "let me look", "let me first")
BUILTIN_BAD_PHRASES = (
    "(standards basis)", "(file location verified)", "I'm applying the BIA facilitation method",
    "Nothing has been saved.", "byte-identical to the referee-validated record", "reply exactly:",
)

# ---- per-turn predicates (pure) ---------------------------------------------------------------
# On a multi-party channel the opening `@Name` is delivery, not prose — Hans complained three
# times when it named the wrong person, so it is load-bearing. 35 of Bruno's 43 turns since
# 2026-08-19 open with one. Reading it as the turn's first line hid the stage card underneath
# it (measured 2026-08-20: card_turns 0 on a turn whose second line was the card) and would
# hide an announcement opener the same way.
def is_address_line(line: str) -> bool: return line.strip().startswith("@")

def first_nonempty_line(turn: str) -> str:
    """The turn's first line of prose — the relay address above it is not one."""
    return next((line.strip() for line in turn.splitlines()
                 if line.strip() and not is_address_line(line)), "")

def is_card_turn(turn: str) -> bool: return bool(CARD_RE.match(first_nonempty_line(turn)))
def is_next_turn(turn: str) -> bool: return any(l.strip().startswith("Next:") for l in turn.splitlines())
# A turn whose newlines arrived as two characters renders as one wall with a visible \n and no
# markdown at all. Not ours (codex-acp 1.4.0, session-scoped, predates the voice work) — but the
# owner caught it by eye on a run this script had already graded PASS.
def is_escaped_newline_turn(turn: str) -> bool: return "\\n" in turn
def last_nonempty_line(turn: str) -> str:
    return next((line.strip() for line in reversed(turn.splitlines()) if line.strip()), "")

def is_option_menu(text: str) -> bool:
    """Two or more options, numbered consecutively from 1 or 2 — however laid out or punctuated.
    Consecutiveness is what keeps "the scope covers 4 sites and 6 activities" from reading as a
    menu: a real menu starts at the top and does not skip. The unit lookahead in OPTION_RE is
    load-bearing too — without it the impact grid ("2 h, 8 h") and "RTO 1 h and MTPD 2 days"
    both read as choices."""
    ds = sorted({int(d) for d in OPTION_RE.findall(text)})
    return len(ds) >= 2 and ds[0] in (1, 2) and ds == list(range(ds[0], ds[0] + len(ds)))

def last_block(turn: str) -> list[str]:
    """The closing block: the run of non-empty lines after the final blank line. That is what a
    reader's eye lands on, and it is where a menu lives."""
    out = []
    for line in reversed(turn.splitlines()):
        if not line.strip():
            if out:
                break
            continue
        out.append(line)
    return list(reversed(out))

def is_open_ended(turn: str) -> bool:
    """The turn leaves the reader something to do: a question, or a numbered choice.

    Judged on the CLOSING BLOCK, not the whole turn — a turn that asked its question in the
    middle and then trailed off into prose is a dead end whatever it contains further up.
    Within that block, either the last line asks or offers, or the block carries a numbered menu
    — two or more options, however they are laid out or punctuated.

    That last clause is not generosity: a live turn on 2026-08-19 closed with `1 definition —
    …` / `2 unrecorded — …` on separate lines and scored as a dead end, because reading only
    the final line sees a statement. A false FAIL is the failure mode that had B7 marking
    corrected behaviour down this morning.

    Still a heuristic — it will pass a turn ending "…or 2 of them?" meaninglessly. Tighten it
    when a real run produces a false PASS, not before."""
    block = last_block(turn)
    if not block:
        return False
    if is_option_menu("\n".join(block)):
        return True
    last = block[-1].strip()
    return last.endswith("?") or bool(OPEN_RE.search(last))

def is_multilink_turn(turn: str) -> bool: return len(LINK_RE.findall(turn)) > 1
def is_narration_opener(turn: str) -> bool: return first_nonempty_line(turn).lower().startswith(NARRATION_OPENERS)
def has_nothing_saved(turn: str) -> bool: return "nothing has been saved" in turn.lower()
def has_bad_echo(turn: str, phrases) -> bool: return any(p in turn for p in phrases)

# ---- aggregate counters over turns: list[str] ---------------------------------------------------
def _lens(turns: list[str]) -> list[int]: return [len(t) for t in turns]
def median_chars(turns: list[str]): return statistics.median(_lens(turns)) if turns else 0
def max_chars(turns: list[str]): return max(_lens(turns)) if turns else 0

def p90_chars(turns: list[str]):
    lens = sorted(_lens(turns))
    if not lens:
        return 0
    return lens[math.ceil(0.9 * len(lens)) - 1]  # index of the 90th percentile in the sorted list

def count_card_turns(turns): return sum(1 for t in turns if is_card_turn(t))
def count_next_turns(turns): return sum(1 for t in turns if is_next_turn(t))
def count_escaped_newlines(turns): return sum(1 for t in turns if is_escaped_newline_turn(t))
def count_open_endings(turns): return sum(1 for t in turns if is_open_ended(t))
def count_multilink_turns(turns): return sum(1 for t in turns if is_multilink_turn(t))
def count_narration_openers(turns): return sum(1 for t in turns if is_narration_opener(t))
def count_nothing_saved(turns): return sum(1 for t in turns if has_nothing_saved(t))

def count_bad_echoes(turns: list[str], extra_phrases=()) -> int:
    phrases = (*BUILTIN_BAD_PHRASES, *extra_phrases)
    return sum(1 for t in turns if has_bad_echo(t, phrases))

def compute_counters(turns: list[str], extra_bad_phrases=()) -> dict:
    return {
        "turns": len(turns), "median_chars": median_chars(turns), "p90_chars": p90_chars(turns),
        "max_chars": max_chars(turns), "card_turns": count_card_turns(turns),
        "next_turns": count_next_turns(turns), "open_endings": count_open_endings(turns),
        "escaped_newlines": count_escaped_newlines(turns),
        "multilink_turns": count_multilink_turns(turns),
        "narration_openers": count_narration_openers(turns),
        "nothing_saved": count_nothing_saved(turns),
        "bad_echoes": count_bad_echoes(turns, extra_bad_phrases),
    }

def load_bad_phrases(path: Path = journeys.PERSONAS_FILE) -> list[str]:
    """design/personas.json -> the bia-facilitator persona's examples[*].bad, when that key
    exists (it doesn't yet — stays empty until the concurrent voice/examples task lands)."""
    try:
        personas = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    persona = next((p for p in personas if p.get("id") == "bia-facilitator"), {})
    return [ex["bad"] for ex in persona.get("examples") or [] if ex.get("bad")]

# ---- verdict -------------------------------------------------------------------------------
def verdict(counters: dict, *, max_median=700, max_chars_limit=None, expect_card="any"):
    reasons = []
    # First on purpose: this is the requirement, the eight below are prohibitions. Guarded on
    # `turns` so an empty run stays the "turns: 0" exit-2 case instead of also inventing a
    # voice defect where there was no voice.
    if counters["turns"] and counters["open_endings"] < counters["turns"]:
        reasons.append(f"open_endings {counters['open_endings']} < turns {counters['turns']}")
    if counters["median_chars"] > max_median:
        reasons.append(f"median_chars {counters['median_chars']} > {max_median}")
    if max_chars_limit is not None and counters["max_chars"] > max_chars_limit:
        reasons.append(f"max_chars {counters['max_chars']} > {max_chars_limit}")
    if expect_card == "yes" and counters["card_turns"] == 0:
        reasons.append("expect-card yes but card_turns == 0")
    if expect_card == "no" and counters["card_turns"] > 0:
        reasons.append(f"expect-card no but card_turns == {counters['card_turns']}")
    if counters["bad_echoes"] > 0:
        reasons.append(f"bad_echoes == {counters['bad_echoes']}")
    if counters["narration_openers"] > 0:
        reasons.append(f"narration_openers == {counters['narration_openers']}")
    if counters["nothing_saved"] > 0:
        reasons.append(f"nothing_saved == {counters['nothing_saved']}")
    if counters["escaped_newlines"] > 0:
        reasons.append(f"escaped_newlines == {counters['escaped_newlines']} "
                       f"(harness, not the payload — restart the seat and re-run)")
    if counters["next_turns"] > 0:
        # 2026-08-19: the label is retired everywhere, so any turn carrying it is a defect —
        # not, as before, only one carrying it without a card.
        reasons.append(f"next_turns == {counters['next_turns']}")
    return ("FAIL" if reasons else "PASS"), reasons

def print_report(counters: dict, status: str, reasons: list[str]) -> None:
    for k, v in counters.items():
        print(f"{k}: {v}")
    print(f"verdict: {status}")
    for r in reasons:
        print(f"fail: {r}")

# ---- --file ----------------------------------------------------------------------------------
def turns_from_file(path: Path) -> list[str]:
    turns, current = [], []
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line == "----":
            turns.append("\n".join(current).strip("\n"))
            current = []
        else:
            current.append(line)
    turns.append("\n".join(current).strip("\n"))
    return [t for t in turns if t.strip()]

# ---- --transcripts -----------------------------------------------------------------------------
def default_transcripts_dir() -> Path:
    base = os.environ.get("BIA_WORKFLOW_DATA_DIR")
    return Path(base) / "bia-usage" if base else Path("data/bia-usage")

def turns_from_transcripts(dirpath: Path) -> list[str]:
    turns = []
    for p in sorted(dirpath.glob("transcripts-*.jsonl")):
        for row in ud._read_jsonl(p):
            try:
                content = json.loads(row.get("content") or "{}")
            except ValueError:
                content = {}
            for a in content.get("activities") or []:
                if a.get("type") == "message" and not ud._is_user(a) and a.get("text"):
                    turns.append(a["text"])
    return turns

# ---- --relay -----------------------------------------------------------------------------------
def filter_relay_turns(events: list[dict], author: str) -> list[str]:
    # The live relay returns kind and created_at as INTS (measured 2026-08-19 against
    # #bia-run); the CLI's own examples show them quoted. Compare as text so both shapes work —
    # a mismatch here reads as "the agent said nothing", which is the one failure this tool
    # must never invent.
    kept = [e for e in events if e.get("pubkey") == author and str(e.get("kind")) == "9"]
    kept.sort(key=lambda e: int(e.get("created_at") or 0))
    return [e.get("content", "") for e in kept]

def fetch_relay_turns(channel: str, since, author: str) -> list[str]:
    cmd = ["buzz", "messages", "get", "--channel", channel]
    if since is not None:
        cmd += ["--since", str(since)]
    cmd += ["--limit", "500"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return filter_relay_turns(json.loads(result.stdout), author)

# ---- --payload -----------------------------------------------------------------------------------
def stage_payload_rows() -> list[dict]:
    journey = journeys.load_journeys()["run-bia"]
    total = len(journey.stages)
    rows = []
    for i, stage in enumerate(journey.stages, start=1):
        payload = journeys.render_stage_tool(journey, stage, i, total)
        key_sizes = {k: len(json.dumps(v, ensure_ascii=False)) for k, v in payload.items()}
        rows.append({"stage_id": stage.id, "card": payload["card"],
                     "size": len(json.dumps(payload, ensure_ascii=False)), "key_sizes": key_sizes})
    return rows

def print_payload_report(rows: list[dict]) -> None:
    for r in rows:
        print(f"stage: {r['stage_id']} — {r['card']} — {r['size']} chars")
        for k, sz in sorted(r["key_sizes"].items(), key=lambda kv: -kv[1]):
            print(f"  {k}: {sz}")
    print(f"protocol_chars: {len(journeys.STAGE_PROTOCOL)}")
    print(f"payload_sum: {sum(r['size'] for r in rows)}")
    print(f"payload_max: {max((r['size'] for r in rows), default=0)}")

# ---- CLI -----------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="voice_check.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--relay", metavar="CHANNEL", help="read turns from a relay channel")
    p.add_argument("--since", type=int, default=None, help="relay: only events at/after this unix ts")
    p.add_argument("--author", default=BRUNO_PUBKEY, help="relay: pubkey to keep (default: Bruno)")
    p.add_argument("--transcripts", nargs="?", const="", default=None, metavar="DIR",
                   help="read turns from transcripts-*.jsonl")
    p.add_argument("--file", metavar="PATH", help="read turns from a file, split on a '----' line")
    p.add_argument("--payload", action="store_true", help="print the stage-payload budget table")
    p.add_argument("--max-median", type=int, default=700, dest="max_median")
    p.add_argument("--max-chars", type=int, default=None, dest="max_chars")
    p.add_argument("--expect-card", choices=["yes", "no", "any"], default="any", dest="expect_card")
    return p

def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv[1:])

    if args.payload:
        print_payload_report(stage_payload_rows())
        return 0

    if args.relay:
        try:
            turns = fetch_relay_turns(args.relay, args.since, args.author)
        except subprocess.CalledProcessError as e:
            msg = (e.stderr or str(e)).strip()
            print(msg.splitlines()[-1] if msg else str(e), file=sys.stderr)
            return 2
    elif args.transcripts is not None:
        turns = turns_from_transcripts(Path(args.transcripts) if args.transcripts else default_transcripts_dir())
    elif args.file:
        turns = turns_from_file(Path(args.file))
    else:
        print("error: specify one of --relay, --transcripts, --file, --payload", file=sys.stderr)
        return 2

    if not turns:
        print("turns: 0")
        return 2

    counters = compute_counters(turns, load_bad_phrases())
    status, reasons = verdict(counters, max_median=args.max_median,
                              max_chars_limit=args.max_chars, expect_card=args.expect_card)
    print_report(counters, status, reasons)
    return 0 if status == "PASS" else 1

if __name__ == "__main__":
    sys.exit(main(sys.argv))
