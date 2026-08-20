#!/usr/bin/env python3
"""Deterministic BIA referee — the "machine floor" for the Copilot BIA lane.

The check logic is COPIED VERBATIM from the paused demo engine, which is the source of truth:
  * /opt/aibcm-demo/render.py   -> validate, _grid_problems, _worst_case_row, _parse_dur,
                                   _norm, _has_plan_language + constants
  * /opt/aibcm-demo/bcm_file.py -> derive_mtpd, check_monotonic, _NO_MTPD
Copied, not imported, because /opt/aibcm-demo is not a package and is PAUSED — a sys.path
import would couple this live server's health to a dormant repo (design spec D-B).
Two engine branches are intentionally NOT ported (v1): prior_cycle_ref file resolution and the
dependency-register cross-check — both filesystem/state lanes (spec §4). Re-sync is a diff away.
(The per-activity `dependencies` id check added 2026-07-30 is the narrower successor of the
second: exact register asset ids only, resolved in the wrapper where the register is in hand.)

Public surface:
  * validate_record(record, method, sources) -> list[str]   # the six checks (pure, stdlib-only)
  * validate_bia_record(company, record) -> dict            # server-side fetch + PASS/reject
"""
from __future__ import annotations

import json
import re
import unicodedata

import graph_files

# ── constants (verbatim from render.py) ──────────────────────────────────────
_PLAN_MARKERS = [
    "immediate actions", "recovery actions", "invocation trigger", "roles and responsibilities",
    "roles & responsibilities", "action card", "declare an incident", "declare incident",
    "0-1 hour", "0–1 hour", "step 1:", "activate the plan",
]
_AI_WORDS = {"ai", "assistant", "llm", "model", "gpt", "claude", "glm", "bot", "agent"}
_PP4_PREFIX = re.compile(r"^(gap|option|human decisions?|missing evidence)\b")
_BIA_EVIDENCE_TYPES = {"transcript_quote", "prior_bia", "prior_cycle", "company_file"}


_PUNCT_EQUIVALENTS = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    "\u00a0": " ", "\u202f": " ",
})


def _norm(s: str) -> str:
    """Comparison normalization, not rewriting: tolerate typography/encoding equivalents.

    Word order and contiguity remain intact, so stitched or paraphrased quotes still fail.
    """
    text = unicodedata.normalize("NFKC", str(s)).translate(_PUNCT_EQUIVALENTS).lower()
    return re.sub(r"\s+", " ", re.sub(r"[*_`]", "", text)).strip()


def pp4_missing(register: dict, handoff_text: str) -> list[str]:
    """Register pp4_issue ids absent from the handoff text (ledger #11 / lesson #26) — the
    write jaw for output/<bia>/pp4-handoff.md (graph_files.write_file). _norm absorbs typographic
    hyphens — PE-ZERLEG-01 was dropped six runs in a row."""
    handoff_norm = _norm(handoff_text)
    return [aid for aid, entry in register.items()
            if isinstance(entry, dict) and entry.get("pp4_issue")
            and _norm(aid) not in handoff_norm]


_DUR_UNITS = {"min": 1, "mins": 1, "minute": 1, "minutes": 1, "m": 1,
              "h": 60, "hr": 60, "hrs": 60, "hour": 60, "hours": 60,
              "d": 1440, "day": 1440, "days": 1440,
              # English-delta (spec §5): "1 week" is a live horizon in the marschkamp matrix;
              # the engine's German units lacked it, which silently no-op'd the RTO<MTPD check.
              "week": 10080, "weeks": 10080}


def _parse_dur(s) -> int | None:
    """Duration → minutes. Ranges ('2-4 hours') use the LOWER bound (conservative: the deadline
    is the band's start). Returns None when unparseable — callers must treat None as 'cannot
    compare', never as zero.

    Comparison-normalization, not rewriting: a trailing qualifier note (the captures write the
    RTO as "8 h (line)") and the ≤/≥ forms of the method's "< 8 h" rto_map are tolerated — only
    the numeric clock matters for the RTO<MTPD check. A genuinely non-numeric target ('same day')
    still returns None so the caller teaches the expected format."""
    if not s:
        return None
    t = str(s).strip().lower()
    t = re.sub(r"\s*\([^)]*\)\s*$", "", t)  # drop a trailing "(...)" qualifier note
    t = t.lstrip("<>≤≥≈~ ").strip()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(?:[-–]\s*\d+(?:\.\d+)?)?\s*([a-z]+)$", t)
    if not m or m.group(2) not in _DUR_UNITS:
        return None
    return int(float(m.group(1)) * _DUR_UNITS[m.group(2)])


def _squash(s) -> str:
    """Typography key: normalize (case, whitespace, hyphens) then drop all spaces, so '24 h' and
    '24h' collapse to one key while a meaningful parenthetical like '(ERP order data)' is kept."""
    return re.sub(r"\s+", "", _norm(s))


def _same_horizon(a, b) -> bool:
    """True if two labels name the same time horizon despite typography: '24h' == '24 h', and
    '24 hours' == '24 h' by duration. Ranges ('0–4 h') match by key, not by lower-bound duration."""
    if _squash(a) == _squash(b):
        return True
    da, db = _parse_dur(a), _parse_dur(b)
    return da is not None and db is not None and da == db


def _has_plan_language(text: str) -> bool:
    n = _norm(text)
    return any(m in n for m in _PLAN_MARKERS)


def _looks_like_source_path(value: str) -> bool:
    value = str(value).strip().lower()
    return "/" in value and value.endswith((".md", ".json", ".jsonl", ".txt"))


def _source_file(source_path: str, files: dict[str, str]) -> tuple[str, str] | None:
    """Resolve relative or company-prefixed source paths without guessing filenames."""
    wanted = str(source_path).strip().lstrip("/")
    if wanted in files:
        return wanted, files[wanted]
    matches = [(path, text) for path, text in files.items()
               if wanted.endswith("/" + path) or path.endswith("/" + wanted)]
    return matches[0] if len(matches) == 1 else None


def _grid_problems(qid: str, grid: dict, method: dict, evidence: list | None = None,
                   shared_quote_ack: dict | None = None) -> list[str]:
    """Structural rules for one question's impact_grid against the method matrix.
    A lens-level "MISSING" cell is an honest state (flagged, not blocked); a horizon with NO
    scored lens at all is a hard block — you can be honest about one lens, not skip a time band.
    `evidence` carries the question's evidence items for the lens rule (a lens scored >= 2 needs
    a quote tagged with that lens); `None` switches that one rule off, everything else still runs."""
    errors: list[str] = []
    lenses = {s.get("id") for s in method.get("scenarios", [])}
    horizons = method.get("time_horizons", [])
    for lens in grid:
        if lens not in lenses:
            errors.append(f"question {qid}: impact_grid lens {lens!r} is not a method scenario id")
    missing_lenses = sorted(lenses - set(grid))
    if missing_lenses:
        errors.append(f"question {qid}: impact_grid missing lens row(s): {missing_lenses}")
    for lens, row in grid.items():
        if lens not in lenses:
            continue
        if not isinstance(row, dict):
            errors.append(f"question {qid}: impact_grid[{lens!r}] must be an object of {{horizon: score}}, got {type(row).__name__}")
            continue
        extra_h = [h for h in row if h not in horizons]
        if extra_h:
            errors.append(f"question {qid}: impact_grid[{lens!r}] has unknown horizon(s): {extra_h}")
        missing_h = [h for h in horizons if h not in row]
        if missing_h:
            errors.append(f"question {qid}: impact_grid[{lens!r}] missing horizon(s): {missing_h}")
        for h, v in row.items():
            if v != "MISSING" and not (isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 5):
                errors.append(f"question {qid}: impact_grid[{lens!r}][{h!r}] invalid score {v!r} (1-5 or 'MISSING')")
    # run (b) 2026-08-18 (Hans's non-wording item): a lens is a claim once any cell is >= 2, and a
    # claim needs the quote that supports it — tagged, so the check can tell which quote is about
    # which category. Score 1 is 'negligible' and needs no quote; MISSING stays the honest state.
    # `None` means approved before this rule — an unchanged saved activity is not a new claim.
    if evidence is not None:
        tagged = {e.get("lens") for e in evidence if isinstance(e, dict)}
        for lens, row in grid.items():
            if lens in lenses and isinstance(row, dict) and lens not in tagged \
                    and any(isinstance(v, int) and not isinstance(v, bool) and v >= 2
                            for v in row.values()):
                errors.append(f"question {qid}: impact_grid[{lens!r}] is scored but no evidence "
                              f"item carries lens {lens!r} — tag the quote that supports the "
                              f"score with lens={lens!r}, or set the row to 'MISSING' (an "
                              f"unasked category stays open)")
        # readiness.md H1, cheap half: the rule above proves a scored lens HAS a quote, never that
        # the quote is ABOUT it — so one real quote copied under seven lens values satisfies all
        # seven. Leo's audit #1 is the live instance. Detecting reuse needs no per-lens vocabulary.
        # Hans ruled the rule 2026-08-19, asked as the manager who signs: not banned, because
        # "1 zwingt mich ein zweites zitat zu erfinden wenn der mann nur einen satz gesagt hat —
        # erfundene belege sind schlimmer als wiederverwendete", and not a threshold, because
        # "3 ist eine willkuerliche grenze, die lernt jeder in zwei laeufen auszunutzen, dann steht
        # am ende alles auf genau zwei". It is put in front of whoever signs instead.
        # His two conditions are why this is an object and not a `_flagged` boolean like its
        # siblings: the acknowledgement must name the affected lenses and carry a human's name,
        # "sonst ist es ein klick".
        # ponytail: the inverse case is NOT caught — a real quote filed under "unmodelled findings"
        # instead of carrying a lens stays invisible. Hans named it as the half that actually
        # annoyed him; it needs per-lens vocabulary, so it stays open as readiness.md H1's
        # remaining half rather than being guessed at here.
        scored = {lens for lens, row in grid.items()
                  if lens in lenses and isinstance(row, dict)
                  and any(isinstance(v, int) and not isinstance(v, bool) and v >= 2
                          for v in row.values())}
        by_quote: dict[str, set] = {}
        for e in evidence:
            if isinstance(e, dict) and e.get("lens") in scored:
                text = _norm(e.get("quote", e.get("ref", "")))
                if text:
                    by_quote.setdefault(text, set()).add(e["lens"])
        ack = shared_quote_ack if isinstance(shared_quote_ack, dict) else {}
        named = {l for l in (ack.get("lenses") or []) if isinstance(l, str)}
        who = str(ack.get("approved_by") or "").strip()
        for text, shared in sorted(by_quote.items()):
            if len(shared) > 1 and not (who and shared <= named):
                errors.append(
                    f"question {qid}: the same quote is the evidence for {sorted(shared)} — one "
                    f"quote cannot be what supports {len(shared)} different impact categories "
                    f"unless a human says so. Quote the span that supports each category "
                    f"separately, or record "
                    f'{{"shared_quote_ack": {{"lenses": {sorted(shared)}, '
                    f'"approved_by": "<name of the person approving>"}}}}'
                )
    if not errors:
        for h in horizons:
            if not any(isinstance(row.get(h), int) for row in grid.values()):
                errors.append(f"question {qid}: impact_grid horizon {h!r} has no scored lens (all MISSING) — cannot derive MTPD")
    return errors


def _canonicalize_horizon_keys(grid: dict, method: dict) -> dict:
    """Accept typographic equivalents such as `0-4 h` for canonical `0–4 h`.

    Unknown labels are retained so `_grid_problems` can reject them explicitly. Collisions are
    also retained rather than silently overwriting a value.
    """
    horizons = method.get("time_horizons", [])
    by_norm = {_norm(h): h for h in horizons}
    out: dict = {}
    for lens, row in grid.items():
        if not isinstance(row, dict):
            out[lens] = row
            continue
        canonical_row: dict = {}
        for raw_h, value in row.items():
            canonical = by_norm.get(_norm(raw_h), raw_h)
            key = raw_h if canonical in canonical_row else canonical
            canonical_row[key] = value
        out[lens] = canonical_row
    return out


def _worst_case_row(grid: dict) -> dict:
    """Collapse a per-lens grid to the worst-case score per horizon (MISSING cells excluded).
    This is the flat {horizon: score} shape the derivation functions consume."""
    row: dict = {}
    for lens_row in grid.values():
        if not isinstance(lens_row, dict):
            continue
        for h, v in lens_row.items():
            if isinstance(v, int) and not isinstance(v, bool):
                row[h] = max(row.get(h, 0), v)
    return row


# ── deterministic derivation (verbatim from bcm_file.py) ─────────────────────
_NO_MTPD = "no MTPD within horizons"


def check_monotonic(grid: dict, method: dict) -> list[str]:
    """Worst-case impact must not fall as the outage lengthens. Returns problems ([] = pass)."""
    hs = [h for h in (method.get("time_horizons") or []) if h in grid]
    return [f"impact drops from {grid[a]} at {a} to {grid[b]} at {b}"
            for a, b in zip(hs, hs[1:]) if grid[b] < grid[a]]


def derive_mtpd(grid: dict, method: dict) -> str:
    """Earliest horizon whose worst-case rating >= intolerability threshold; else _NO_MTPD."""
    thr = method.get("intolerability_threshold")
    for h in method.get("time_horizons") or []:
        if h in grid and grid[h] >= thr:
            return h
    return _NO_MTPD


# ── the six checks (validate() from render.py, deps/prior_cycle branches dropped) ─
def validate_record(record: dict, method: dict, sources: dict | None = None) -> list[str]:
    """Return blocking teaching rejections ([] = PASS). `sources` maps an evidence type -> the
    source text its quotes must appear in (e.g. {"transcript_quote": <all interviews>,
    "prior_bia": <prior BIA>}); a type absent from `sources` is checked structurally only.
    `method` is the org's method matrix (method.json)."""
    errors: list[str] = []
    sources = sources or {}
    source_files = sources.get("__files__", {}) if isinstance(sources.get("__files__"), dict) else {}
    nsrc = {t: _norm(txt) for t, txt in sources.items()
            if t != "__files__" and isinstance(txt, str)}
    qids = {q.get("id") for q in record.get("questions", [])}
    method_lenses = {s.get("id") for s in method.get("scenarios", [])} if method is not None else set()

    if not record.get("questions"):
        errors.append("no questions — a BIA must record at least one critical activity")
    if method is not None and record.get("questions"):
        if not any(isinstance(q.get("impact_grid"), dict) for q in record["questions"]):
            errors.append("method matrix supplied but no question has a scored impact_grid (step 3: score impact over time)")
        if not any(q.get("recovery_target") for q in record["questions"]):
            errors.append(
                "method matrix supplied but no question sets a recovery_target — add a recovery "
                "question such as {\"id\":\"cut-recovery\",\"recovery_target\":\"8 h\","
                "\"impact_ref\":\"cut-impact\"}, where cut-impact is the id of the scored "
                "impact question"
            )

    for q in record.get("questions", []):
        qid = q.get("id", "?")
        missing = str(q.get("status", "")).upper() == "MISSING"
        if q.get("answer") and not missing and not q.get("evidence"):
            errors.append(f"question {qid}: answered without evidence and not marked MISSING")
        for e in q.get("evidence", []):
            if not isinstance(e, dict):  # some models emit a bare quote string; reject, don't crash the gate
                errors.append(f"question {qid}: malformed evidence item (expected object with type/quote/source_path, got {type(e).__name__})")
                continue
            t = e.get("type")
            legacy_ref = e.get("ref", "")
            quote = e.get("quote", legacy_ref)
            source_path = e.get("source_path", "")
            equote = _norm(quote)
            lens = e.get("lens")
            if lens is not None and method is not None and lens not in method_lenses:
                errors.append(f"question {qid}: evidence lens {lens!r} is not a method scenario id")
            if t not in _BIA_EVIDENCE_TYPES:
                errors.append(f"question {qid}: unknown evidence type {t!r} (allowed: {sorted(_BIA_EVIDENCE_TYPES)})")
            elif not e.get("quote") and _looks_like_source_path(legacy_ref):
                errors.append(
                    f"question {qid}: evidence.ref contains a file path, not a quote; use "
                    f"{{\"type\":{t!r},\"quote\":\"exact contiguous text\","
                    f"\"source_path\":{legacy_ref!r}}}"
                )
            elif not equote:
                errors.append(f"question {qid}: {t} evidence quote is empty")
            elif source_path and source_files:
                resolved = _source_file(source_path, source_files)
                if not resolved:
                    errors.append(f"question {qid}: evidence source_path not found or ambiguous: {source_path!r}")
                elif equote not in _norm(resolved[1]):
                    errors.append(
                        f"question {qid}: {t} quote not found in {resolved[0]!r} "
                        f"(possible fabrication): {quote!r}"
                    )
            elif t in nsrc and equote not in nsrc[t]:
                errors.append(f"question {qid}: {t} quote not found in its source (possible fabrication): {quote!r}")
            elif not source_path and t not in nsrc:
                # Leo's audit 2026-08-19: with the shared chat-interview blob gone, a quote whose
                # type has no readable source reached the end of this chain unchecked and passed.
                # An unverifiable quote is not a verified one — say so, and name what would fix it.
                errors.append(
                    f"question {qid}: {t} evidence cannot be verified — no {t} source is readable "
                    f"for this company, so nothing was checked. Cite the file the words come from "
                    f"in source_path (an interview held in chat: "
                    f"{_ROLE_PATHS['chat_interviews']}/<the transcript you saved>.md)")
        if q.get("recovery_target"):
            ref = q.get("impact_ref")
            if not ref:
                errors.append(f"question {qid}: recovery_target set but no impact_ref (unlinked target)")
            elif ref not in qids:
                errors.append(f"question {qid}: impact_ref '{ref}' does not match any question id")
        if _has_plan_language(q.get("answer", "")):
            errors.append(f"question {qid}: plan-drafting language in answer (PP4 boundary — options/questions only)")
        if method is not None:
            grid = q.get("impact_grid")
            if isinstance(grid, dict):
                grid = _canonicalize_horizon_keys(grid, method)
                # `approved_before` is minted by the wrapper for an activity identical to the copy
                # already saved: the lens rule judges a claim made now, not one already on disk.
                evidence = None if q.get("approved_before") else q.get("evidence", [])
                gp = _grid_problems(qid, grid, method, evidence, q.get("shared_quote_ack"))
                errors.extend(gp)
                if not gp:
                    worst = _worst_case_row(grid)
                    errors.extend(f"question {qid}: impact grid not monotonic: {p}"
                                  for p in check_monotonic(worst, method))
                    want = derive_mtpd(worst, method)
                    cur = q.get("mtpd")
                    if cur is None:
                        errors.append(f"question {qid}: impact_grid present but no mtpd — record the grid-derived MTPD")
                    elif not _same_horizon(cur, want):
                        errors.append(f"question {qid}: mtpd {cur!r} does not match the grid-derived MTPD {want!r} (MTPD must come from the grid)")
            elif grid is not None:
                errors.append(f"question {qid}: impact_grid must be an object of {{lens: {{horizon: score}}}}, got {type(grid).__name__}")
            elif q.get("mtpd") is not None:
                errors.append(f"question {qid}: mtpd {q['mtpd']!r} without an impact_grid — MTPD must come from a scored grid")

    if method is not None:
        by_id = {q.get("id"): q for q in record.get("questions", [])}
        vocab = method.get("rpo_vocabulary") or []
        vocab_keys = {_squash(v) for v in vocab}
        for q in record.get("questions", []):
            qid = q.get("id", "?")
            if q.get("rpo") is not None and _squash(q["rpo"]) not in vocab_keys:
                # §5: name the allowed vocabulary — the Y1 audit finding turned into a guardrail.
                errors.append(f"question {qid}: rpo {q['rpo']!r} not in the method's rpo_vocabulary — allowed: {vocab}")
            rt, ref = q.get("recovery_target"), q.get("impact_ref")
            if rt and ref and ref in by_id:
                iq = by_id[ref]
                if not isinstance(iq.get("impact_grid"), dict):
                    errors.append(f"question {qid}: impact question '{ref}' has no impact_grid (method requires scored impact-over-time)")
                    continue
                mtpd = iq.get("mtpd")
                mtpd_min = _parse_dur(mtpd)
                if mtpd_min is None:
                    continue  # 'no MTPD within horizons' (or mtpd errors already reported above)
                rt_min = _parse_dur(rt)
                if rt_min is None:
                    errors.append(
                        f"question {qid}: recovery_target {rt!r} is not a parseable duration — "
                        f"give a bare number+unit like '8 h', '24 h', or '2-4 hours' so the RTO "
                        f"can be compared to the MTPD (a trailing note in parentheses is ignored)"
                    )
                elif rt_min >= mtpd_min and not q.get("recovery_gap_flagged"):
                    errors.append(f"question {qid}: recovery_target {rt} >= MTPD {mtpd} ({ref}) — unacknowledged recovery gap; set recovery_gap_flagged or fix the target")

    if record.get("plan") is not None:
        errors.append("plan content present in `plan` — PP4 boundary violated (V1 stops before plan drafting)")
    for item in record.get("pp4_handoff", []):
        if not _PP4_PREFIX.match(_norm(item)):
            errors.append(f"pp4_handoff item must start with Gap:/Option:/Human decisions:/Missing evidence: — got {item!r}")
        if _has_plan_language(item):
            errors.append(f"pp4_handoff item contains plan-drafting language (PP4 boundary): {item!r}")

    for c in record.get("comparison", []):
        who = c.get("escalated_to", "")
        if not who:
            errors.append(f"comparison '{c.get('issue', '?')}': no escalated_to (a conflict must escalate to a human)")
        elif set(re.findall(r"[a-z]+", who.lower())) & _AI_WORDS:
            errors.append(f"comparison '{c.get('issue', '?')}': escalated_to names the AI, not a human ({who!r})")
        if _has_plan_language(c.get("why_it_matters", "")):
            errors.append(f"comparison '{c.get('issue', '?')}': plan-drafting language in comparison (PP4 boundary)")
    return errors


# ── server-side fetch wrapper ────────────────────────────────────────────────
# Folder roles resolved in ONE place (spec §3) — a future re-layout is a one-line edit here,
# not a hunt through the code. Paths reflect the 2026-07-20 role-folder reorg.
_ROLE_PATHS = {
    "method": "02_BCM-Method/method.json",
    "interviews": "07_Interviews",         # folder; every child concatenated into one blob
    # 2026-08-17 (W33 digest): an interview held in chat has no file under 07_Interviews —
    # its transcript is saved to output/owner-interviews/ (the only folder the agent may
    # write), so that folder is a quote source too. Willem's 12.08 run had no quote source.
    "chat_interviews": "output/owner-interviews",
    "prior_bia": "08_Prior-Cycle",         # folder; filenames may be date-prefixed
    "dependencies": "03_Dependencies/dependency-register.json",
    "record": "output/bia-record.json",    # the agent's artifact; passed in as `record` in v1
}


def _read_folder_files(company: str, folder: str) -> dict[str, str]:
    """Read direct child files, retaining exact relative paths for provenance checks."""
    listing = graph_files.list_files(company, folder)
    if "error" in listing:
        return {}
    files: dict[str, str] = {}
    for f in listing.get("files", []):
        if f.get("is_folder"):
            continue
        path = f"{folder}/{f['name']}"
        got = graph_files.read_file(company, path)
        if "error" not in got:
            files[path] = got.get("content", "")
    return files


def _recovery_target_scalar(value):
    """Unwrap an unambiguous transport wrapper without changing the recovery decision.

    Copilot sometimes serialises a scalar RTO as {"rto": "< 8 h"}. That is a mechanical
    shape error, not a human decision. Ambiguous objects still reach the strict referee.
    """
    if isinstance(value, dict) and len(value) == 1:
        key, scalar = next(iter(value.items()))
        if key in {"rto", "duration", "value"} and isinstance(scalar, (str, int, float)):
            return scalar
    return value


def _adapt_activity_record(record: dict, approved_before: frozenset = frozenset()) -> dict:
    """Translate the typed, agent-friendly activity contract to the proven question referee.

    Legacy question records remain accepted by the pure referee and direct callers.

    `approved_before` is the set of positional indexes into `record["activities"]` whose claim is
    unchanged from the saved copy (computed in `_validate_bia_record`); their impact question is
    marked so the lens rule skips it. Positional, not by id: live records carry no `id`, so every
    saved activity would adapt to the same `activity-impact` question id.
    """
    if record.get("questions"):
        adapted = dict(record)
        # The mark is minted here from the saved record, never accepted from the caller.
        adapted["questions"] = [
            {k: (_recovery_target_scalar(v) if k == "recovery_target" else v)
             for k, v in question.items() if k != "approved_before"}
            for question in record["questions"]
        ]
        return adapted
    if not record.get("activities"):
        return record
    questions: list[dict] = []
    for index, activity in enumerate(record["activities"]):
        aid = str(activity.get("id", "activity"))
        impact_id = f"{aid}-impact"
        recovery_id = f"{aid}-recovery"
        evidence = activity.get("evidence", [])
        impact = {
            "id": impact_id,
            "question": f"Impact over time for {activity.get('name', aid)}",
            "answer": activity.get("impact_summary", ""),
            "status": "answered",
            "impact_grid": activity.get("impact_grid"),
            "mtpd": activity.get("mtpd"),
            "rpo": activity.get("rpo"),
            "evidence": evidence,
            # Rides with `evidence` because the shared-quote rule is judged on the impact grid.
            # Dropping it here would make that rule unanswerable on a live record: reuse blocks
            # the save and no field the drafter can set would ever clear it.
            "shared_quote_ack": activity.get("shared_quote_ack"),
        }
        if index in approved_before:
            impact["approved_before"] = True
        recovery = {
            "id": recovery_id,
            "question": f"Recovery target for {activity.get('name', aid)}",
            "answer": activity.get("recovery_summary", ""),
            "status": "answered",
            "recovery_target": _recovery_target_scalar(activity.get("recovery_target")),
            "impact_ref": impact_id,
            "recovery_gap_flagged": bool(activity.get("recovery_gap_flagged", False)),
            "evidence": evidence,
        }
        questions.extend((impact, recovery))
    adapted = {k: v for k, v in record.items() if k != "activities"}
    adapted["questions"] = questions
    return adapted


def dependency_problems(record: dict, register: dict | None) -> list[str]:
    """Per-activity `dependencies` (2026-07-30 contract bundle): shape is a list of exact
    register asset id strings; ids resolve against the register when it is readable
    (`register` None = not checkable, shape only). An unmodeled dependency stays a
    prose finding — the graph and grader tolerate foreign shapes, the gate does not.

    REQUIRED since 2026-07-31, where it used to be optional. `if deps is None: continue`
    plus a journey that said "omit the field when none apply" is how marschkamp's one
    activity was saved with no linkage at all — its Stage-3 analysis named six providers
    by exact register id, the register carried all six on AN-SCHLACHT-01, and the record
    carried none of them. Nothing complained until the dependency graph drew the activity
    as a card with no edges. The check is evidence-backed: with no readable register there
    are no ids to demand, so absence still degrades to shape-only."""
    errors: list[str] = []
    register_ids = None if register is None else set(register)
    activities = record.get("activities") if isinstance(record, dict) else None
    for act in activities or []:
        if not isinstance(act, dict):
            continue
        aid = act.get("id") or act.get("name") or "?"
        # `dept` rides in this loop rather than getting a check of its own: it is the same
        # rule — a BIA activity that cannot be tied back to the register is not finished.
        # It is the only field that lines an activity up against the register's consumers
        # lines, and it is required from the day it was added (2026-07-31), because an
        # optional field the journey merely mentions is exactly how `dependencies` went
        # missing. A field nothing populates is a dead field.
        if register_ids and not str(act.get("dept") or "").strip():
            errors.append(f"activity {aid}: no department recorded — name the department "
                          "that performs it, using the same dept value the dependency "
                          "register uses on its consumers entries (e.g. 'schlachtung')")
        deps = act.get("dependencies")
        if deps is None or (isinstance(deps, list) and not deps):
            # An empty list is the same silence as an absent field, so both land here.
            if register_ids:
                errors.append(f"activity {aid}: no dependencies recorded — list the exact "
                              f"register asset ids this activity needs (known: "
                              f"{sorted(register_ids)}). An activity that needs nothing "
                              "in the register is a gap in the record; a dependency the "
                              "register does not model is a finding to raise, not a "
                              "field to omit")
            continue
        if not isinstance(deps, list):
            errors.append(f"activity {aid}: dependencies must be a list of exact register "
                          f"asset ids, got {type(deps).__name__}")
            continue
        breaches: list[tuple[str, str]] = []   # (asset id, its recorded clock)
        clocked = 0                            # providers whose clock is readable at all
        for dep in deps:
            if not isinstance(dep, str) or not dep.strip():
                errors.append(f"activity {aid}: dependencies entries must be exact register "
                              f"asset id strings, got {dep!r}")
            elif register_ids is not None and dep not in register_ids:
                errors.append(f"activity {aid}: dependency {dep!r} is not in the dependency "
                              f"register — use the exact register id (known: "
                              f"{sorted(register_ids)}); an unmodeled dependency is a "
                              "finding, not an invented id")
            elif register and not act.get("dependency_gap_flagged"):
                # The method's minimum principle (design/run-bia.yaml stage 3): "a provider's
                # MTPD/RTO must not exceed the requirement of any consumer that depends on it".
                # `rto` is the operative clock — when the provider is BACK; its own mtpd is its
                # own tolerance, not its recovery. `>=` because equal is not less-than: Hans,
                # 2026-08-19, "genau zur MTPD zurueckkommen ist schon gerissen, nicht knapp …
                # der puffer ist null". Its own flag, never `recovery_gap_flagged` — that one
                # answers for the activity's own target, and one flag for both would let an
                # acknowledged RTO gap hide five unacknowledged provider breaches. Per activity
                # rather than per dependency because the acknowledgement is one human decision
                # about one activity — and since 2026-08-20 the rejection is printed that way
                # too, one line per activity naming every provider (see the emit below).
                # ponytail: a provider with no parseable `rto` is skipped, not flagged —
                # register completeness is a different hole. Add it when the register is
                # required to carry a clock for every asset.
                rto, mtpd = register.get(dep, {}).get("rto"), act.get("mtpd")
                rto_min, mtpd_min = _parse_dur(rto), _parse_dur(mtpd)
                if rto_min is not None and mtpd_min is not None:
                    clocked += 1
                    if rto_min >= mtpd_min:
                        breaches.append((dep, rto))
        # One finding per activity, not one per provider. Hans, 2026-08-20, holding the 17 live
        # rejections: "fifteen lines carrying the same sentence is one finding, not fifteen —
        # print the sentence once and list the assets under it." The acknowledgement was already
        # per activity; only the printing was per dependency, so the signer did the grouping by
        # hand. Every provider still appears by name with its own clock — nobody acknowledges a
        # list they have not read.
        if breaches:
            named = ", ".join(f"{dep} ({rto})" for dep, rto in breaches)
            mtpd = act.get("mtpd")
            if len(breaches) == clocked and clocked >= 2:
                # His sharper point, and the reason this is not just formatting: when NO provider
                # meets the target, the target is the outlier, not the providers. "an 8 h mtpd
                # where every single provider comes back at 8 or 24 means either the mtpd was
                # written without asking IT, or IT numbers were never tested. that is one
                # conversation, not fifteen flags." Naming the MTPD is what stops the answer from
                # being fifteen acknowledgements — the padding move he ruled against on quotes.
                errors.append(
                    f"activity {aid}: every provider with a recorded clock returns at or after "
                    f"this activity's MTPD {mtpd} — {named}. When none of them meets it, the "
                    "MTPD is the number to question, not the providers: it was set without "
                    "asking the resource owners, or their figures were never tested. That is "
                    "one conversation, not one flag per provider — dependency_gap_flagged here "
                    "records the gap without answering it")
            else:
                errors.append(
                    f"activity {aid}: providers returning at or after this activity's MTPD "
                    f"{mtpd} — {named}. A provider back at or after the MTPD leaves zero buffer "
                    "(method minimum principle). Fix the target, escalate it, or set "
                    "dependency_gap_flagged once you have decided which")
    # W9, the machine-checkable half. Stage 5 already asks for "a provider target looser than
    # a consumer needs" and for requirements that "look implausible next to their peers", and
    # nothing enforced either. This is the H2 minimum principle applied across consumers
    # instead of within one activity: when two departments lean on one asset, the SHORTEST
    # MTPD binds, because a provider that satisfies the most tolerant consumer still fails the
    # least tolerant one. Pure function of the saved record, which already carries every
    # department in one file — so it needs none of the consolidation decision.
    # ponytail: W9's other half (HR asks 110 % of the seats, trading 5 %) is NOT here — the
    # record carries no seat or headcount figures at all. It needs new record fields, and it
    # is named as out of scope in readiness.md rather than half-built.
    ack = record.get("cross_department_ack") if isinstance(record, dict) else None
    ack = ack if isinstance(ack, dict) else {}
    ack_assets = {a for a in (ack.get("assets") or []) if isinstance(a, str)}
    ack_by = str(ack.get("approved_by") or "").strip()
    consumers: dict[str, dict[str, int]] = {}   # asset -> {dept: shortest mtpd in minutes}
    for act in activities or []:
        if not isinstance(act, dict):
            continue
        dept = str(act.get("dept") or "").strip()
        mtpd_min = _parse_dur(act.get("mtpd"))
        if not dept or mtpd_min is None:
            continue
        for dep in act.get("dependencies") or []:
            if not isinstance(dep, str) or not dep.strip():
                continue
            row = consumers.setdefault(dep, {})
            row[dept] = min(row.get(dept, mtpd_min), mtpd_min)
    for asset, by_dept in sorted(consumers.items()):
        if len(by_dept) < 2 or len(set(by_dept.values())) < 2:
            continue
        if ack_by and asset in ack_assets:
            continue
        strict = min(by_dept, key=lambda d: by_dept[d])
        loose = max(by_dept, key=lambda d: by_dept[d])
        errors.append(
            f"dependency {asset}: departments disagree on when it must be back — "
            f"{strict} needs it inside its MTPD while {loose} tolerates longer. The strictest "
            f"consumer binds, so {asset} must meet {strict}'s requirement. Record the binding "
            f"requirement, or acknowledge it with "
            f'{{"cross_department_ack": {{"assets": ["{asset}"], '
            f'"approved_by": "<name of the person approving>"}}}}'
        )
    return errors


# Same relative path as the record the graph renders from; literal here so the pure
# referee never imports the Graph lane's constants.
_RECORD_PATH = "output/bia-record.json"


def _activity_key(activity: dict) -> str:
    # Name first, id second. `name` is the graph's node id and the register join
    # key (since 78af195), and live records carry no `id` at all — keying on a
    # shared or absent id collapses two different processes into one card.
    return str(activity.get("name") or activity.get("id") or "").strip().casefold()


def _merge_saved_activities(record: dict, company: str) -> tuple[dict, str | None, dict]:
    """Upsert the incoming activities onto the ones already saved.

    The dependency graph renders its BIA activity cards from `output/bia-record.json`
    and nothing else, so a record carrying only the process just analysed erases every
    process analysed before it. Observed live 2026-08-03: a Cutting run replaced the
    Slaughter record and the Slaughter card vanished from the published graph.

    Matching is by id-or-name. A re-run of the same process replaces its entry in
    place, a new process is appended, and the prior order is preserved so existing
    cards keep their position. Accumulate-only by design: there is no removal path
    here, because dropping an activity is a deliberate act and belongs in a tool
    that says so out loud.

    This runs BEFORE the PASS mints a save_token, which is the only place it can:
    the token binds the record's canonical bytes and the write is by reference, so
    merging any later would persist bytes the referee never validated.

    Also returns the saved activities keyed by `_activity_key` ({} when there is no saved
    record or it is the legacy question form) — the caller needs them to tell an activity
    re-drafted now from one it is merely carrying forward.
    """
    if record.get("questions") or not isinstance(record.get("activities"), list):
        return record, None, {}  # legacy question form, or nothing to merge onto
    saved = graph_files.read_file(company, _RECORD_PATH)
    if "error" in saved:
        return record, None, {}  # no record yet — the first BIA for this company
    try:
        prior = json.loads(saved["content"])
    except json.JSONDecodeError as exc:
        return record, (
            f"the saved {_RECORD_PATH} is not valid JSON ({exc}) — refusing to validate, "
            "because saving over it would destroy the activities it still holds. Repair or "
            "restore that file first."), {}
    prior_acts = prior.get("activities")
    if not isinstance(prior_acts, list) or not prior_acts:
        return record, None, {}

    incoming = {_activity_key(a): a for a in record["activities"] if isinstance(a, dict)}
    prior_by_key = {_activity_key(a): a for a in prior_acts if isinstance(a, dict)}
    merged = [incoming.get(_activity_key(a), a) for a in prior_acts if isinstance(a, dict)]
    merged += [a for a in record["activities"]
               if isinstance(a, dict) and _activity_key(a) not in prior_by_key]
    return {**record, "activities": merged}, None, prior_by_key


def validate_bia_record(company: str, record) -> dict:
    """Referee wrapper: the verdict plus its next move (2026-08-16 smart next steps) — PASS →
    show the first card; FAIL → fix mechanical rejections yourself; error → unchanged."""
    out = _validate_bia_record(company, record)
    if out.get("pass") is True:
        out["next_move"] = "Show the first activity card and ask Approve / Amend"
    elif out.get("pass") is False:
        out["next_move"] = ("Fix the listed rejections yourself (mechanical) and re-run; ask the "
                            "owner only for judgment calls")
    return out


def _validate_bia_record(company: str, record) -> dict:
    """Referee a drafted BIA record against the company's method + interviews (server-side fetch).
    Returns {"pass": True, "save_token": ...}, {"pass": False, "rejections": [...]}, or
    {"error": ...} on an infrastructure failure (method matrix unreadable). READ-ONLY: judges,
    never writes — a PASS additionally binds the record's canonical bytes to a one-time
    save_token (P7 I-1 part 3) so the save happens by reference, never re-typed."""
    if isinstance(record, str):
        try:
            record = json.loads(record)
        except json.JSONDecodeError as exc:
            return {"pass": False, "rejections": [f"record is not valid JSON: {exc}"]}
    if not isinstance(record, dict):
        return {"pass": False, "rejections": [f"record must be a JSON object, got {type(record).__name__}"]}
    original, merge_error, prior_by_key = _merge_saved_activities(record, company)
    if merge_error:
        return {"error": merge_error}
    # `original` is the activities form as passed, with any previously saved activities
    # preserved — the bytes a save must persist.
    # Grandfather by identity, not provenance: an activity whose `impact_grid` and `evidence`
    # both match the copy already saved under the same key is not a claim made now, so the
    # lens rule (2026-08-18) skips it. Touch either field and it is re-litigated in full.
    approved_before = frozenset(
        index for index, activity in enumerate(original.get("activities") or [])
        if isinstance(activity, dict)
        and _activity_key(activity) in prior_by_key
        and all(prior_by_key[_activity_key(activity)].get(field) == activity.get(field)
                for field in ("impact_grid", "evidence"))
    )
    record = _adapt_activity_record(original, approved_before=approved_before)

    method_file = graph_files.read_file(company, _ROLE_PATHS["method"])
    if "error" in method_file:
        return {"error": f"cannot read the method matrix ({_ROLE_PATHS['method']}): {method_file['error']}"}
    try:
        method = json.loads(method_file["content"])
    except json.JSONDecodeError as exc:
        return {"error": f"method matrix is not valid JSON: {exc}"}

    sources: dict = {}
    source_files = _read_folder_files(company, _ROLE_PATHS["interviews"])
    # D-14 (run (b) 2026-08-18): chat_interviews is the one folder the agent may write, and every
    # direct child was folded into the blob the exact-quote check reads — so Bruno's own stage 1,
    # 2 and 3 sat in it as interview evidence at the 20:57:06Z round. Hans: "if the referee cannot
    # tell my transcript from brunos own three files in the same folder, then the thing guarding
    # my belief was me, not the check." Drop the stage artifacts by the same derived set the write
    # jaw refuses. This does NOT make a chat transcript independent evidence — the agent writes
    # that too; it removes the artifacts that are provably not interviews.
    # Leo's audit 2026-08-19 (objection 5): D-14 dropped the agent's own stage artifacts from the
    # blob, but output/owner-interviews/ is shared by EVERY BIA — the HR onboarding interview and
    # lf-abp-01's owner interview sit in it today, and an anonymous blob let a Slaughter activity
    # satisfy the exact-quote check against either. A chat transcript is evidence for the BIA that
    # names it: these files stay in source_files, so `source_path` still resolves and the quote is
    # checked against that one file, and they no longer join the blob that answers for any of them.
    interviews = "\n\n".join(source_files.values())
    source_files.update({
        path: text
        for path, text in _read_folder_files(company, _ROLE_PATHS["chat_interviews"]).items()
        if path.rsplit("/", 1)[-1].casefold() not in graph_files._legacy_singletons()
    })
    if interviews:
        sources["transcript_quote"] = interviews
    prior_files = _read_folder_files(company, _ROLE_PATHS["prior_bia"])
    source_files.update(prior_files)
    if prior_files:
        prior_text = "\n\n".join(prior_files.values())
        sources["prior_bia"] = prior_text
        sources["prior_cycle"] = prior_text
    register: dict | None = None
    dependency = graph_files.read_file(company, _ROLE_PATHS["dependencies"])
    if "error" not in dependency:
        source_files[_ROLE_PATHS["dependencies"]] = dependency.get("content", "")
        try:
            reg = json.loads(dependency["content"])
            if isinstance(reg, dict):
                # The entries, not only their keys: the minimum-principle check needs each
                # provider's own clock. `synthetic: True` and friends are scalars and drop out.
                register = {k: v for k, v in reg.items() if isinstance(v, dict)}
        except json.JSONDecodeError:
            pass  # unparseable register = existence not checkable; shape check still runs
    source_files[_ROLE_PATHS["method"]] = method_file.get("content", "")
    sources["company_file"] = "\n\n".join(source_files.values())
    sources["__files__"] = source_files

    rejections = validate_record(record, method, sources) \
        + dependency_problems(original, register)
    if rejections:
        return {"pass": False, "rejections": rejections}
    return {"pass": True, "save_token": graph_files.issue_save_token(company, original)}


if __name__ == "__main__":  # self-check: python3 bia_referee.py
    _method = {"time_horizons": ["0–4 h", "8 h", "24 h", "1 week"], "intolerability_threshold": 4,
               "scenarios": [{"id": "financial"}], "rpo_vocabulary": ["4 h (ERP order data)"]}
    _rec = {"dept": "x", "version": 1, "plan": None, "pp4_handoff": [], "comparison": [],
            "questions": [{"id": "q1", "status": "answered", "answer": "a",
                           "impact_grid": {"financial": {"0–4 h": 1, "8 h": 4, "24 h": 5, "1 week": 5}},
                           "mtpd": "8 h", "rpo": "4 h (ERP order data)",
                           "recovery_target": "4 h", "impact_ref": "q1",
                           "evidence": [{"type": "transcript_quote", "ref": "line stops fast"}]}]}
    assert validate_record(_rec, _method, {"transcript_quote": "the line stops fast"}) == [], "valid should pass"
    _rec["questions"][0]["evidence"][0]["ref"] = "invented quote"
    assert any("fabrication" in e for e in validate_record(_rec, _method, {"transcript_quote": "the line stops fast"}))
    print("bia_referee self-check OK")
