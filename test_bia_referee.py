#!/usr/bin/env python3
"""Tests for the BIA referee (bia_referee.py).

The pure checks are ported from the engine selftest (/opt/aibcm-demo/render.py::selftest);
these tests re-prove them against the English marschkamp matrix (spec §5 vocab deltas) plus
the two machine-checkable red-team attacks (fabricated quote, RTO>=MTPD unflagged).
"""
from __future__ import annotations

import copy
import json

import graph_files
import bia_referee
from bia_referee import validate_record, validate_bia_record, _parse_dur

# The live English method matrix (the marschkamp pack's method parameters).
HORIZONS = ["0–4 h", "8 h", "24 h", "48 h", "72 h", "1 week"]
LENSES = ["financial", "customer-delivery", "legal-regulatory", "reputation",
          "animal-welfare-food-safety", "people-safety", "environment"]
METHOD = {
    "time_horizons": HORIZONS,
    "intolerability_threshold": 4,
    "scenarios": [{"id": lens} for lens in LENSES],
    "rpo_vocabulary": ["0 h (no loss)", "4 h (ERP order data)",
                       "24 h (batch/traceability)", "n/a (physical)"],
}
TRANSCRIPT = (
    "Interview with Ahlgrim (Slaughter).\n"
    "Ahlgrim: The line has to keep moving; if it stops we lose product fast.\n"
    "Ahlgrim: Realistically we can be back in about eight hours once power returns."
)


def full_grid(worst_row: dict) -> dict:
    """A complete 7-lens grid whose worst-case row equals `worst_row` (other lenses all 1)."""
    g = {lens: {h: 1 for h in HORIZONS} for lens in LENSES}
    g["financial"] = dict(worst_row)
    return g


def valid_record() -> dict:
    return {
        "dept": "Slaughter", "version": 1, "plan": None,
        "comparison": [], "pp4_handoff": [],
        "questions": [
            {"id": "q1", "question": "impact over time?",
             "answer": "line stops, product at risk", "status": "answered",
             "impact_grid": full_grid({"0–4 h": 1, "8 h": 2, "24 h": 4,
                                       "48 h": 5, "72 h": 5, "1 week": 5}),
             "mtpd": "24 h", "rpo": "4 h (ERP order data)",
             "evidence": [{"type": "transcript_quote", "lens": "financial",
                           "ref": "the line has to keep moving"}]},
            {"id": "q2", "question": "realistic RTO?",
             "answer": "eight hours", "status": "answered",
             "recovery_target": "8 h", "impact_ref": "q1",
             "evidence": [{"type": "transcript_quote",
                           "ref": "we can be back in about eight hours"}]},
        ],
    }


def valid_activity_record() -> dict:
    impact = valid_record()["questions"][0]
    return {
        "dept": "Slaughter", "version": 1, "plan": None,
        "comparison": [], "pp4_handoff": [],
        "activities": [{
            "id": "slaughter", "name": "Slaughter",
            "dept": "zerlegung",  # required since 2026-07-31: the register join key
            "impact_grid": impact["impact_grid"], "mtpd": impact["mtpd"],
            "rpo": impact["rpo"], "recovery_target": "8 h",
            "evidence": [{
                "type": "transcript_quote",
                "lens": "financial",
                "quote": "The line has to keep moving",
                "source_path": "07_Interviews/2026-06-01-ahlgrim-interview.md",
            }],
        }],
    }


SOURCES = {"transcript_quote": TRANSCRIPT}


# ── the happy path ───────────────────────────────────────────────────────────
def test_valid_record_passes():
    assert validate_record(valid_record(), METHOD, SOURCES) == []


def test_scored_lens_without_a_lens_tagged_quote_is_rejected():
    """run (b) 2026-08-18: Bruno wrote 'I will keep unsupported category scores open' in Stage 3
    and scored Financial 3 across five horizons off no euro figure in Stage 4; the referee PASSed
    it because evidence is required per activity, not per lens. Hans: 'it should not need me'."""
    rec = valid_record()
    rec["questions"][0]["impact_grid"]["customer-delivery"]["24 h"] = 3
    # the only evidence item is tagged financial; nothing supports customer-delivery
    errs = validate_record(rec, METHOD, SOURCES)
    assert any("customer-delivery" in e and "no evidence item carries lens" in e for e in errs), errs


def test_lens_at_baseline_needs_no_quote_and_missing_is_still_honest():
    """Score 1 (negligible) on a lens nobody raised is a defensible default; MISSING stays the
    honest state. Only a claim (>=2) needs its quote."""
    rec = valid_record()
    rec["questions"][0]["impact_grid"]["environment"] = {h: "MISSING" for h in HORIZONS}
    assert validate_record(rec, METHOD, SOURCES) == []


def test_scored_lens_with_no_evidence_key_at_all_is_still_rejected():
    """Task 1 review (fix round 1): a 'questions'-shaped record whose question omits `evidence`
    entirely (not just an empty list) must not bypass the lens check — `q.get("evidence")` with
    no default returns None, and `_grid_problems` only ran the check when `evidence is not None`."""
    rec = valid_record()
    del rec["questions"][0]["evidence"]
    errs = validate_record(rec, METHOD, SOURCES)
    assert any("no evidence item carries lens 'financial'" in e for e in errs), errs


def test_evidence_lens_not_a_method_scenario_id_is_rejected():
    rec = valid_record()
    rec["questions"][0]["evidence"][0]["lens"] = "not-a-real-lens"
    errs = validate_record(rec, METHOD, SOURCES)
    assert any("evidence lens 'not-a-real-lens' is not a method scenario id" in e for e in errs), errs


# ── §5 parsing deltas ────────────────────────────────────────────────────────
def test_parse_dur_en_dash_band_uses_lower_bound():
    assert _parse_dur("0–4 h") == 0


def test_parse_dur_one_week_is_a_real_duration():
    # English-delta: "1 week" is a live horizon; the numeric RTO<MTPD check must not silently skip it.
    assert _parse_dur("1 week") == 7 * 24 * 60


def test_parse_dur_tolerates_a_trailing_qualifier_note():
    # Live Stage 6 blocker (2026-07-20): the Stage 3/4 captures write the Slaughter RTO as
    # "8 h (line)"; the "(line)" note must not make the RTO uncomparable to the MTPD.
    assert _parse_dur("8 h (line)") == 480
    assert _parse_dur("8 h (slaughter line)") == 480


def test_parse_dur_tolerates_less_or_equal_sign():
    # The method's rto_map uses "< 8 h"; an agent may render the same clock as "≤ 8 h".
    assert _parse_dur("≤ 8 h") == 480
    assert _parse_dur("≥ 24 h") == 1440


def test_recovery_target_with_capture_line_qualifier_is_accepted():
    # The exact live failure: the Slaughter recovery_target carried the capture's "(line)" qualifier.
    r = valid_record()
    r["questions"][1]["recovery_target"] = "8 h (line)"
    assert validate_record(r, METHOD, SOURCES) == []


def test_adapter_unwraps_object_shaped_rto_without_a_human_gate():
    # Live Stage 6 friction: Copilot sent {"rto": "< 8 h"}. The value is unchanged, so this is
    # a mechanical transport repair and must not create a blocker artifact or approval request.
    r = valid_activity_record()
    r["activities"][0]["recovery_target"] = {"rto": "< 8 h"}
    adapted = bia_referee._adapt_activity_record(r)
    assert adapted["questions"][1]["recovery_target"] == "< 8 h"
    assert validate_record(adapted, METHOD, SOURCES) == []


def test_unparseable_rto_is_rejected_with_an_actionable_example():
    # A genuinely vague RTO is still rejected — but the teaching message must show the expected
    # bare-duration format so the agent self-corrects instead of asking the human.
    r = valid_record()
    r["questions"][1]["recovery_target"] = "same day"
    errs = validate_record(r, METHOD, SOURCES)
    msg = next((e for e in errs if "recovery_target" in e and "same day" in e), "")
    assert msg, errs
    assert "8 h" in msg


def test_mtpd_typography_is_tolerated():
    # The grid derives the horizon label "24 h"; an LLM writes "24h"/"24 hours" for the SAME
    # horizon. Typography must not read as an asserted-vs-derived MTPD mismatch.
    for v in ("24h", "24 hours", "24 h"):
        r = valid_record()
        r["questions"][0]["mtpd"] = v
        assert validate_record(r, METHOD, SOURCES) == [], (v, validate_record(r, METHOD, SOURCES))


def test_mtpd_wrong_horizon_still_rejected():
    # Substance guard: a genuinely different horizon is still a mismatch (grid derives 24 h).
    r = valid_record()
    r["questions"][0]["mtpd"] = "8 h"
    assert any("does not match the grid-derived MTPD" in e for e in validate_record(r, METHOD, SOURCES))


def test_rpo_spacing_is_tolerated():
    # Same vocabulary entry, LLM spacing ("4h" for "4 h"); the meaningful parenthetical is intact.
    r = valid_record()
    r["questions"][0]["rpo"] = "4h (ERP order data)"
    assert validate_record(r, METHOD, SOURCES) == [], validate_record(r, METHOD, SOURCES)


def test_rpo_wrong_parenthetical_still_rejected():
    # Substance guard: the parenthetical distinguishes vocabulary entries — a wrong tag must fail.
    r = valid_record()
    r["questions"][0]["rpo"] = "4 h (ERP)"
    assert any("rpo_vocabulary" in e for e in validate_record(r, METHOD, SOURCES))


# ── (d) RPO must be an exact vocabulary string, teaching the allowed values ───
def test_rpo_terse_string_rejected_naming_vocabulary():
    r = valid_record()
    r["questions"][0]["rpo"] = "4 h"  # register-style terse — not the method string
    errs = validate_record(r, METHOD, SOURCES)
    assert any("rpo_vocabulary" in e and "4 h (ERP order data)" in e for e in errs), errs


def test_rpo_exact_string_accepted():
    r = valid_record()
    r["questions"][0]["rpo"] = "24 h (batch/traceability)"
    assert validate_record(r, METHOD, SOURCES) == []


# ── (e) fabricated quote (red-team RT-6) ─────────────────────────────────────
def test_fabricated_quote_rejected():
    r = valid_record()
    r["questions"][0]["evidence"] = [
        {"type": "transcript_quote", "ref": "we will be fully back within five minutes, guaranteed"}]
    errs = validate_record(r, METHOD, SOURCES)
    assert any("fabrication" in e for e in errs), errs


def test_stitched_quote_with_ellipsis_is_not_treated_as_verbatim():
    r = valid_record()
    r["questions"][0]["evidence"] = [
        {"type": "transcript_quote",
         "ref": "The line has to keep moving … we can be back in about eight hours"}
    ]
    errs = validate_record(r, METHOD, SOURCES)
    assert any("fabrication" in e for e in errs), errs


def test_quote_typography_variants_are_accepted_without_weakening_contiguity():
    r = valid_record()
    r["questions"][0]["evidence"] = [
        {"type": "transcript_quote", "lens": "financial", "ref": "It's ready - within eight hours."}
    ]
    sources = {"transcript_quote": TRANSCRIPT + "\nOwner: It’s ready — within eight hours."}
    assert validate_record(r, METHOD, sources) == []


def test_explicit_quote_and_source_path_are_checked_against_that_file():
    r = valid_record()
    r["questions"][0]["evidence"] = [{
        "type": "transcript_quote",
        "lens": "financial",
        "quote": "The line has to keep moving",
        "source_path": "07_Interviews/2026-06-01-ahlgrim-interview.md",
    }]
    sources = dict(SOURCES)
    sources["__files__"] = {
        "07_Interviews/2026-06-01-ahlgrim-interview.md": TRANSCRIPT,
    }
    assert validate_record(r, METHOD, sources) == []


def test_file_path_in_legacy_ref_gets_a_schema_repair_hint():
    r = valid_record()
    r["questions"][0]["evidence"] = [{
        "type": "transcript_quote",
        "ref": "marschkamp/07_Interviews/2026-06-01-ahlgrim-interview.md",
    }]
    errs = validate_record(r, METHOD, SOURCES)
    assert any("file path, not a quote" in e and "source_path" in e for e in errs), errs


def test_wrong_explicit_source_path_is_rejected():
    r = valid_record()
    r["questions"][0]["evidence"] = [{
        "type": "transcript_quote", "quote": "The line has to keep moving",
        "source_path": "07_Interviews/not-there.md",
    }]
    sources = dict(SOURCES)
    sources["__files__"] = {
        "07_Interviews/2026-06-01-ahlgrim-interview.md": TRANSCRIPT,
    }
    errs = validate_record(r, METHOD, sources)
    assert any("source_path not found" in e for e in errs), errs


# ── (c) RTO >= MTPD unflagged ────────────────────────────────────────────────
def test_rto_at_or_above_mtpd_without_flag_rejected():
    r = valid_record()
    r["questions"][1]["recovery_target"] = "48 h"  # >= 24 h MTPD
    errs = validate_record(r, METHOD, SOURCES)
    assert any("recovery gap" in e for e in errs), errs


def test_rto_equal_to_mtpd_without_flag_rejected():
    r = valid_record()
    r["questions"][0]["impact_grid"] = full_grid(
        {"0–4 h": 1, "8 h": 4, "24 h": 5, "48 h": 5, "72 h": 5, "1 week": 5}
    )
    r["questions"][0]["mtpd"] = "8 h"
    r["questions"][1]["recovery_target"] = "8 h"
    errs = validate_record(r, METHOD, SOURCES)
    assert any("recovery gap" in e for e in errs), errs


def test_rto_gap_accepted_when_flagged():
    r = valid_record()
    r["questions"][1]["recovery_target"] = "48 h"
    r["questions"][1]["recovery_gap_flagged"] = True
    assert validate_record(r, METHOD, SOURCES) == []


# ── (f) plan content (PP4 boundary) ──────────────────────────────────────────
def test_plan_key_rejected():
    r = valid_record()
    r["plan"] = {"steps": ["do the thing"]}
    assert any("plan content present" in e for e in validate_record(r, METHOD, SOURCES))


def test_pp4_handoff_plan_language_rejected():
    r = valid_record()
    r["pp4_handoff"] = ["Immediate actions: declare an incident, activate the plan"]
    assert any("plan-drafting language" in e for e in validate_record(r, METHOD, SOURCES))


def test_plan_language_in_answer_rejected():
    r = valid_record()
    r["questions"][0]["answer"] = "Immediate actions: call the electrician"
    assert any("plan-drafting language in answer" in e for e in validate_record(r, METHOD, SOURCES))


# ── (a) grid completeness / whole-empty horizon ──────────────────────────────
def test_whole_empty_horizon_rejected():
    r = valid_record()
    for lens in r["questions"][0]["impact_grid"].values():
        lens["8 h"] = "MISSING"
    assert any("all MISSING" in e for e in validate_record(r, METHOD, SOURCES))


def test_missing_lens_row_rejected():
    r = valid_record()
    del r["questions"][0]["impact_grid"]["environment"]
    assert any("missing lens row" in e for e in validate_record(r, METHOD, SOURCES))


def test_missing_horizon_rejected():
    r = valid_record()
    del r["questions"][0]["impact_grid"]["financial"]["1 week"]
    assert any("missing horizon" in e for e in validate_record(r, METHOD, SOURCES))


def test_ascii_hyphen_horizon_is_canonicalized_to_method_en_dash():
    r = valid_record()
    for row in r["questions"][0]["impact_grid"].values():
        row["0-4 h"] = row.pop("0–4 h")
    assert validate_record(r, METHOD, SOURCES) == []


# ── (b) MTPD must be grid-derived, not asserted ──────────────────────────────
def test_asserted_mtpd_mismatch_rejected():
    r = valid_record()
    r["questions"][0]["mtpd"] = "1 week"  # derived is "24 h"
    assert any("grid-derived MTPD" in e for e in validate_record(r, METHOD, SOURCES))


def test_missing_mtpd_with_grid_rejected():
    r = valid_record()
    del r["questions"][0]["mtpd"]
    assert any("no mtpd" in e for e in validate_record(r, METHOD, SOURCES))


# ── structural guards (no crash, empty BIA) ──────────────────────────────────
def test_empty_bia_rejected():
    errs = validate_record({"dept": "x", "version": 1, "plan": None}, METHOD, SOURCES)
    assert any("no questions" in e for e in errs)


def test_missing_recovery_target_rejection_contains_literal_link_example():
    r = valid_record()
    del r["questions"][1]["recovery_target"]
    errs = validate_record(r, METHOD, SOURCES)
    assert any("cut-recovery" in e and "impact_ref" in e and "cut-impact" in e for e in errs), errs


def test_bare_string_evidence_does_not_crash():
    r = valid_record()
    r["questions"][0]["evidence"] = ["a bare quote string, not an object"]
    assert any("malformed evidence" in e for e in validate_record(r, METHOD, SOURCES))


# ── the server-side fetch wrapper (graph_files monkeypatched) ────────────────
class _FakeGraph:
    """Serves a {relpath: content} map with the same shape as graph_files."""

    def __init__(self, files: dict):
        self.files = files

    def read_file(self, company: str, path: str) -> dict:
        if company != "marschkamp":
            return {"error": f"unknown company '{company}'"}
        if path in self.files:
            body = self.files[path]
            return {"path": f"{company}/{path}", "content": body, "size": len(body)}
        return {"error": f"file not found: {company}/{path}"}

    def list_files(self, company: str, subpath: str = "") -> dict:
        if company != "marschkamp":
            return {"error": f"unknown company '{company}'"}
        prefix = (subpath.rstrip("/") + "/") if subpath else ""
        names = sorted({
            p[len(prefix):] for p in self.files
            if p.startswith(prefix) and "/" not in p[len(prefix):]
        })
        return {"company": company,
                "files": [{"name": n, "is_folder": False, "size": 0, "path": n} for n in names]}


def _install(monkeypatch, files):
    fake = _FakeGraph(files)
    monkeypatch.setattr(graph_files, "read_file", fake.read_file)
    monkeypatch.setattr(graph_files, "list_files", fake.list_files)
    return fake


def _pack() -> dict:
    return {
        "02_BCM-Method/method.json": json.dumps(METHOD),
        "07_Interviews/2026-06-01-ahlgrim-interview.md": TRANSCRIPT,
        "08_Prior-Cycle/prior-bia.md": "Prior cycle: Slaughter MTPD 24 h.",
    }


def test_wrapper_passes_on_valid_record(monkeypatch):
    _install(monkeypatch, _pack())
    out = validate_bia_record("marschkamp", valid_record())
    assert out["pass"] is True and len(out["save_token"]) == 32
    # 2026-08-16 smart next steps: PASS names the next move (show the first card).
    assert out["next_move"].startswith("Show the first activity card")


def test_wrapper_adapts_typed_activity_contract_to_internal_questions(monkeypatch):
    _install(monkeypatch, _pack())
    out = validate_bia_record("marschkamp", valid_activity_record())
    assert out["pass"] is True and len(out["save_token"]) == 32


def test_wrapper_rejects_fabricated_quote(monkeypatch):
    _install(monkeypatch, _pack())
    r = valid_record()
    r["questions"][0]["evidence"] = [
        {"type": "transcript_quote", "ref": "totally invented never-said quote"}]
    out = validate_bia_record("marschkamp", r)
    assert out["pass"] is False
    assert any("fabrication" in e for e in out["rejections"])
    assert out["next_move"].startswith("Fix the listed rejections yourself")


def test_wrapper_accepts_json_string(monkeypatch):
    _install(monkeypatch, _pack())
    out = validate_bia_record("marschkamp", json.dumps(valid_record()))
    assert out["pass"] is True and len(out["save_token"]) == 32


# ── P7 I-1 part 3: a referee PASS binds the validated bytes to a one-time save token ──

def test_wrapper_pass_stores_canonical_bytes_of_the_record_as_passed(monkeypatch):
    """The token binds the ORIGINAL activities-form record (what gets saved), not the
    internally adapted questions form; the server, not the agent, owns the serialisation."""
    _install(monkeypatch, _pack())
    graph_files._validated_records.clear()
    rec = valid_activity_record()
    out = validate_bia_record("marschkamp", rec)
    slot = graph_files._validated_records["marschkamp"]
    assert slot["token"] == out["save_token"]
    assert slot["data"] == (json.dumps(rec, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def test_wrapper_fail_issues_no_token(monkeypatch):
    _install(monkeypatch, _pack())
    graph_files._validated_records.clear()
    r = valid_record()
    r["questions"][0]["evidence"] = [
        {"type": "transcript_quote", "ref": "totally invented never-said quote"}]
    out = validate_bia_record("marschkamp", r)
    assert out["pass"] is False and "save_token" not in out
    assert "marschkamp" not in graph_files._validated_records


def test_wrapper_malformed_json_string_is_a_rejection(monkeypatch):
    _install(monkeypatch, _pack())
    out = validate_bia_record("marschkamp", "{not valid json")
    assert out["pass"] is False
    assert any("json" in e.lower() for e in out["rejections"])


def test_wrapper_missing_method_is_infra_error(monkeypatch):
    files = _pack()
    del files["02_BCM-Method/method.json"]
    _install(monkeypatch, files)
    out = validate_bia_record("marschkamp", valid_record())
    assert "error" in out and "pass" not in out


def test_wrapper_unknown_company_refused(monkeypatch):
    _install(monkeypatch, _pack())
    out = validate_bia_record("acme", valid_record())
    assert "error" in out


def test_wrapper_survives_absent_prior_cycle(monkeypatch):
    files = _pack()
    del files["08_Prior-Cycle/prior-bia.md"]
    _install(monkeypatch, files)
    assert validate_bia_record("marschkamp", valid_record())["pass"] is True


def test_wrapper_discovers_date_prefixed_prior_cycle(monkeypatch):
    files = _pack()
    prior = files.pop("08_Prior-Cycle/prior-bia.md")
    files["08_Prior-Cycle/25_04_15_prior-bia.md"] = prior
    _install(monkeypatch, files)
    assert validate_bia_record("marschkamp", valid_record())["pass"] is True


# ── 2026-07-30 contract bundle: optional per-activity `dependencies` ─────────────────
# Shape is a list of exact register asset ids, resolved against the register when it is
# readable. An unmodeled dependency stays a prose finding — never an invented id.

DEP_REGISTER = {
    "synthetic": True,
    "KA-01": {"asset_id": "KA-01", "owner_name": "R. Boll"},
    "UV-STROM-01": {"asset_id": "UV-STROM-01", "owner_name": None},
}


def _pack_with_register() -> dict:
    files = _pack()
    files["03_Dependencies/dependency-register.json"] = json.dumps(DEP_REGISTER)
    return files


def _record_with_deps(deps) -> dict:
    rec = valid_activity_record()
    rec["activities"][0]["dependencies"] = deps
    return rec


def test_dependencies_of_register_ids_pass_and_ride_the_token(monkeypatch):
    _install(monkeypatch, _pack_with_register())
    graph_files._validated_records.clear()
    out = validate_bia_record("marschkamp", _record_with_deps(["KA-01", "UV-STROM-01"]))
    assert out["pass"] is True, out
    slot = graph_files._validated_records["marschkamp"]
    assert b'"dependencies"' in slot["data"]  # the saved bytes carry the field


def test_dependencies_absent_field_is_rejected(monkeypatch):
    """REVERSAL, 2026-07-31. Through 2026-07-30 an absent `dependencies` passed
    (`if deps is None: continue`) and the journey told the drafter to "omit the field
    when none apply". That combination is how marschkamp's one activity reached the
    live graph as an unlinked card — while its own Stage-3 analysis named six providers
    by exact id and the register carried all six on AN-SCHLACHT-01. The graph was the
    first surface that made it visible; the gate never saw it. A BIA activity that
    needs nothing from the register is a gap in the record, so it is a rejection a
    human reads, not a silent save."""
    _install(monkeypatch, _pack_with_register())
    out = validate_bia_record("marschkamp", valid_activity_record())
    assert out["pass"] is False
    assert any("no dependencies recorded" in e and "KA-01" in e for e in out["rejections"])


def test_activity_without_a_dept_is_rejected(monkeypatch):
    """`dept` is the only field that lines a BIA activity up against the register's own
    consumers lines. Added 2026-07-31 and required in the same breath, because the whole
    lesson of that day was that an optional field the journey merely mentions gets
    omitted — which is exactly how `dependencies` went missing. A field nothing populates
    is a dead field."""
    _install(monkeypatch, _pack_with_register())
    rec = valid_activity_record()
    rec["activities"][0]["dependencies"] = ["KA-01"]
    del rec["activities"][0]["dept"]
    out = validate_bia_record("marschkamp", rec)
    assert out["pass"] is False
    assert any("no department recorded" in e for e in out["rejections"])
    rec["activities"][0]["dept"] = "zerlegung"
    assert validate_bia_record("marschkamp", rec)["pass"] is True


def test_dependencies_empty_list_is_rejected(monkeypatch):
    """An empty list is the same silence as an absent field, one keystroke further on —
    pin it explicitly so a future 'fix' cannot satisfy the gate with []."""
    _install(monkeypatch, _pack_with_register())
    out = validate_bia_record("marschkamp", _record_with_deps([]))
    assert out["pass"] is False
    assert any("no dependencies recorded" in e for e in out["rejections"])


def test_dependencies_absent_still_passes_without_a_register(monkeypatch):
    """The rejection is evidence-backed or it is not made: with no readable register
    there are no ids to demand, so absence stays tolerated — the same shape-only
    degradation the unknown-id check already uses."""
    _install(monkeypatch, _pack())  # no register file in this pack
    assert validate_bia_record("marschkamp", valid_activity_record())["pass"] is True


def test_dependencies_not_a_list_is_rejected(monkeypatch):
    _install(monkeypatch, _pack_with_register())
    out = validate_bia_record("marschkamp", _record_with_deps("KA-01"))
    assert out["pass"] is False
    assert any("list of exact register asset ids" in e for e in out["rejections"])


def test_dependencies_entries_must_be_id_strings(monkeypatch):
    """The gate is stricter than the lenient renderers: dict entries were only ever a
    tolerance for foreign records, not the contract for new ones."""
    _install(monkeypatch, _pack_with_register())
    out = validate_bia_record("marschkamp", _record_with_deps([{"id": "KA-01"}]))
    assert out["pass"] is False
    assert any("exact register asset id strings" in e for e in out["rejections"])


def test_dependencies_unknown_id_teaches_the_known_ids(monkeypatch):
    _install(monkeypatch, _pack_with_register())
    out = validate_bia_record("marschkamp", _record_with_deps(["KA-99"]))
    assert out["pass"] is False
    assert any("'KA-99'" in e and "KA-01" in e and "finding" in e
               for e in out["rejections"])


def test_dependencies_shape_only_when_register_unreadable(monkeypatch):
    """No readable register → existence is not checkable; shape still is. Mirrors the
    referee's absent-prior-cycle tolerance rather than failing the whole record."""
    _install(monkeypatch, _pack())  # no register file in this pack
    out = validate_bia_record("marschkamp", _record_with_deps(["KA-99"]))
    assert out["pass"] is True
    out2 = validate_bia_record("marschkamp", _record_with_deps("KA-99"))
    assert out2["pass"] is False  # shape check still bites




# ── H1 (cheap half): one quote cannot be the evidence for many lenses ────────
# readiness.md H1 — the referee checks that a scored lens CARRIES a lens-tagged quote, not that
# the quote is ABOUT that lens, so one real quote copied under seven lens values passes. Leo's
# audit #1 is the live instance. Detecting reuse needs no per-lens vocabulary, so it is the cheap
# 80% of that hole; the semantic "is this quote about this lens" half stays open in readiness.md.

# Scores a lens as a claim (>= 2) only at the late horizons where `financial` is already 5, so
# the worst-case row — and therefore the derived MTPD — is untouched. Scoring a lens 3 everywhere
# instead pushes the grid to "no MTPD within horizons" and the test then fails on arithmetic
# rather than on the rule under test.
_CLAIM_ROW = {"0–4 h": 1, "8 h": 1, "24 h": 1, "48 h": 3, "72 h": 3, "1 week": 3}


def _record_sharing_one_quote(lenses, quote="The line has to keep moving") -> dict:
    """One question that makes a claim on every lens in `lenses` and hands each the same quote."""
    rec = valid_record()
    q = rec["questions"][0]
    for lens in lenses:
        if lens != "financial":
            q["impact_grid"][lens] = dict(_CLAIM_ROW)
    q["evidence"] = [
        {"type": "transcript_quote", "lens": lens, "quote": quote} for lens in lenses
    ]
    return rec


def test_one_quote_under_many_scored_lenses_is_rejected():
    """The exact shape of Leo's #1: real quote, honestly sourced, copied across every category
    so each one looks evidenced. True under any reuse rule — three is never plausible."""
    rec = _record_sharing_one_quote(
        ["financial", "customer-delivery", "legal-regulatory", "reputation"])
    errors = validate_record(rec, METHOD, SOURCES)
    assert any("same quote" in e.lower() or "reused" in e.lower() for e in errors), errors


def test_distinct_quotes_per_lens_pass():
    """The guard against a check that fires on any multi-lens evidence list at all."""
    rec = valid_record()
    q = rec["questions"][0]
    q["impact_grid"]["customer-delivery"] = dict(_CLAIM_ROW)
    q["evidence"] = [
        {"type": "transcript_quote", "lens": "financial",
         "quote": "The line has to keep moving"},
        {"type": "transcript_quote", "lens": "customer-delivery",
         "quote": "if it stops we lose product fast"},
    ]
    assert not [e for e in validate_record(rec, METHOD, SOURCES)
                if "same quote" in e.lower() or "reused" in e.lower()], \
        validate_record(rec, METHOD, SOURCES)


def test_a_quote_reused_on_unscored_lenses_is_not_the_failure():
    """A lens scored 1 is 'negligible' and needs no quote at all, so sharing one with it proves
    nothing false. Only lenses that actually make a claim (>= 2) count."""
    rec = valid_record()
    q = rec["questions"][0]
    q["evidence"] = [
        {"type": "transcript_quote", "lens": "financial",
         "quote": "The line has to keep moving"},
        {"type": "transcript_quote", "lens": "environment",
         "quote": "The line has to keep moving"},
    ]
    assert not [e for e in validate_record(rec, METHOD, SOURCES)
                if "same quote" in e.lower() or "reused" in e.lower()], \
        validate_record(rec, METHOD, SOURCES)


def test_shared_quote_ack_naming_the_lenses_and_a_human_passes():
    """Hans's ruling, 2026-08-19: reuse is not banned — banning it "zwingt mich ein zweites zitat
    zu erfinden wenn der mann nur einen satz gesagt hat, und erfundene belege sind schlimmer als
    wiederverwendete". It is put in front of whoever signs."""
    rec = _record_sharing_one_quote(["financial", "customer-delivery"])
    rec["questions"][0]["shared_quote_ack"] = {
        "lenses": ["financial", "customer-delivery"], "approved_by": "H. Ahlgrim"}
    assert not [e for e in validate_record(rec, METHOD, SOURCES)
                if "same quote" in e.lower()], validate_record(rec, METHOD, SOURCES)


def test_shared_quote_ack_without_a_human_name_is_refused():
    """"sonst ist es ein klick" — an acknowledgement nobody signed is not an acknowledgement."""
    rec = _record_sharing_one_quote(["financial", "customer-delivery"])
    rec["questions"][0]["shared_quote_ack"] = {
        "lenses": ["financial", "customer-delivery"]}
    assert any("same quote" in e.lower() for e in validate_record(rec, METHOD, SOURCES))


def test_shared_quote_ack_must_name_every_affected_lens():
    """Naming one of the three categories is not naming what was approved."""
    rec = _record_sharing_one_quote(["financial", "customer-delivery", "legal-regulatory"])
    rec["questions"][0]["shared_quote_ack"] = {
        "lenses": ["financial"], "approved_by": "H. Ahlgrim"}
    errors = validate_record(rec, METHOD, SOURCES)
    assert any("same quote" in e.lower() for e in errors), errors


def test_shared_quote_ack_survives_the_activity_adapter(monkeypatch):
    """A live record is activity-shaped, not question-shaped. If the adapter drops the ack the
    check becomes unanswerable: reuse blocks the save and no field can clear it."""
    _install(monkeypatch, _pack_with_register())
    rec = _record_with_deps(["KA-01", "UV-STROM-01"])
    act = rec["activities"][0]
    act["impact_grid"] = dict(act["impact_grid"])
    act["impact_grid"]["customer-delivery"] = dict(_CLAIM_ROW)
    act["evidence"] = [
        {"type": "transcript_quote", "lens": lens,
         "quote": "The line has to keep moving",
         "source_path": "07_Interviews/2026-06-01-ahlgrim-interview.md"}
        for lens in ("financial", "customer-delivery")
    ]
    blocked = validate_bia_record("marschkamp", rec)
    assert blocked["pass"] is False
    assert any("same quote" in e.lower() for e in blocked["rejections"]), blocked

    act["shared_quote_ack"] = {"lenses": ["financial", "customer-delivery"],
                               "approved_by": "H. Ahlgrim"}
    out = validate_bia_record("marschkamp", rec)
    assert not [e for e in out.get("rejections", []) if "same quote" in e.lower()], out

# ── H2, the minimum principle: a provider's clock against the consumer's MTPD ─
# design/run-bia.yaml already states it — "a provider's MTPD/RTO must not exceed the
# requirement of any consumer that depends on it; flag every provider whose target is
# looser than a consumer needs" — and nothing enforced it. Leo's audit #2: five
# dependencies in the Slaughter record return at exactly the activity's MTPD while the
# handover's top line reads PASS. Hans, asked as the manager who signs it (2026-08-19,
# #bia-workflow): "genau zur MTPD zurueckkommen ist schon gerissen, nicht knapp. gleich
# ist nicht kleiner, der puffer ist null — das muss der referee rot melden, nicht PASS."
# So the comparison is >=, the same one the activity's own recovery_target already uses.

CLOCK_REGISTER = {
    "synthetic": True,
    # returns exactly at the activity's 24 h MTPD — zero buffer, Hans's case
    "KA-01": {"asset_id": "KA-01", "owner_name": "R. Boll", "rto": "24 h"},
    "UV-STROM-01": {"asset_id": "UV-STROM-01", "owner_name": None, "rto": "4 h"},
}


def _pack_with_clocks() -> dict:
    files = _pack()
    files["03_Dependencies/dependency-register.json"] = json.dumps(CLOCK_REGISTER)
    return files


# Hans, 2026-08-20 00:25, reading the 17 live rejections: "dedupe it. fifteen lines carrying the
# same sentence is one finding, not fifteen — print the sentence once and list the assets under
# it." Three breaching providers on one activity, one safe, so the roll-up below stays off.
MANY_CLOCK_REGISTER = {
    "synthetic": True,
    "KA-01": {"asset_id": "KA-01", "owner_name": "R. Boll", "rto": "24 h"},
    "UV-CO2-01": {"asset_id": "UV-CO2-01", "owner_name": "R. Boll", "rto": "24 h"},
    "LF-ABP-01": {"asset_id": "LF-ABP-01", "owner_name": "R. Boll", "rto": "48 h"},
    "UV-STROM-01": {"asset_id": "UV-STROM-01", "owner_name": None, "rto": "4 h"},
}


def _pack_with_many_clocks() -> dict:
    files = _pack()
    files["03_Dependencies/dependency-register.json"] = json.dumps(MANY_CLOCK_REGISTER)
    return files


def _provider_lines(out) -> list:
    return [e for e in out.get("rejections", []) if "MTPD" in e and "activity" in e
            and "departments" not in e]


def test_one_activity_s_provider_breaches_are_one_finding_not_one_each(monkeypatch):
    """Hans got fifteen lines of the same sentence and could not act on them. The
    acknowledgement was already per activity — the printing was not."""
    _install(monkeypatch, _pack_with_many_clocks())
    out = validate_bia_record(
        "marschkamp", _record_with_deps(["KA-01", "UV-CO2-01", "LF-ABP-01", "UV-STROM-01"]))
    lines = _provider_lines(out)
    assert len(lines) == 1, lines
    assert all(a in lines[0] for a in ("KA-01", "UV-CO2-01", "LF-ABP-01")), lines[0]
    assert "UV-STROM-01" not in lines[0], "a provider that meets the MTPD is not a finding"


def test_every_provider_missing_names_the_mtpd_as_the_suspect(monkeypatch):
    """His second point, which is the one that matters: "an 8 h mtpd where every single
    provider comes back at 8 or 24 means either the mtpd was written without asking IT, or
    IT numbers were never tested. that is one conversation, not fifteen flags." """
    _install(monkeypatch, _pack_with_many_clocks())
    out = validate_bia_record("marschkamp", _record_with_deps(["KA-01", "UV-CO2-01", "LF-ABP-01"]))
    lines = _provider_lines(out)
    assert len(lines) == 1, lines
    assert "MTPD is the number to question" in lines[0], lines[0]


def test_some_providers_missing_does_not_blame_the_mtpd(monkeypatch):
    """The guard: with a provider that meets the target, the target is not the suspect."""
    _install(monkeypatch, _pack_with_many_clocks())
    out = validate_bia_record(
        "marschkamp", _record_with_deps(["KA-01", "UV-CO2-01", "UV-STROM-01"]))
    lines = _provider_lines(out)
    assert len(lines) == 1, lines
    assert "MTPD is the number to question" not in lines[0], lines[0]


def test_dependency_returning_exactly_at_mtpd_is_rejected(monkeypatch):
    """Equal is not less-than. The provider is back at the same hour the activity has
    already become intolerable, so the consumer's requirement is not met."""
    _install(monkeypatch, _pack_with_clocks())
    out = validate_bia_record("marschkamp", _record_with_deps(["KA-01", "UV-STROM-01"]))
    assert out["pass"] is False, out
    assert any("KA-01" in e and "24 h" in e and "MTPD" in e for e in out["rejections"]), \
        out["rejections"]


def test_dependency_returning_inside_the_mtpd_passes(monkeypatch):
    """The guard against a check that simply flags every dependency it can parse."""
    _install(monkeypatch, _pack_with_clocks())
    out = validate_bia_record("marschkamp", _record_with_deps(["UV-STROM-01"]))
    assert out["pass"] is True, out


def test_acknowledged_dependency_gap_passes(monkeypatch):
    """Same escape hatch as the activity's own recovery gap: the breach may stay in the
    record, but only once a human has written down that they know. Its own field, never
    `recovery_gap_flagged` — one flag silencing both would let an acknowledged RTO gap
    hide five unacknowledged dependency breaches, which is the failure this check exists
    to catch."""
    _install(monkeypatch, _pack_with_clocks())
    rec = _record_with_deps(["KA-01", "UV-STROM-01"])
    rec["activities"][0]["dependency_gap_flagged"] = True
    out = validate_bia_record("marschkamp", rec)
    assert out["pass"] is True, out


def test_acknowledging_the_activity_rto_gap_does_not_silence_a_dependency(monkeypatch):
    """The overload bug, pinned: `recovery_gap_flagged` answers for the activity's own
    target and must not answer for its providers."""
    _install(monkeypatch, _pack_with_clocks())
    rec = _record_with_deps(["KA-01"])
    rec["activities"][0]["recovery_gap_flagged"] = True
    out = validate_bia_record("marschkamp", rec)
    assert out["pass"] is False, out
    assert any("KA-01" in e for e in out["rejections"]), out["rejections"]


def _two_department_record(tighten_first=True) -> dict:
    """Two activities in different departments leaning on the same register asset.
    The first is made stricter by moving its grid, never by typing an mtpd:
    financial['8 h'] = 4 reaches the intolerability threshold one horizon earlier,
    so derive_mtpd returns '8 h' against the default '24 h'."""
    base = valid_activity_record()
    first = base["activities"][0]
    first["dept"] = "schlachtung"
    first["dependencies"] = ["KA-01"]
    if tighten_first:
        grid = {k: dict(v) for k, v in first["impact_grid"].items()}
        grid["financial"]["8 h"] = 4
        first["impact_grid"] = grid
        first["mtpd"] = "8 h"
        first["recovery_target"] = "4 h"   # must stay inside the tightened MTPD
    second = json.loads(json.dumps(base["activities"][0]))
    second["id"] = "cutting"
    second["name"] = "Cutting / deboning"
    second["dept"] = "zerlegung"
    second["impact_grid"] = valid_activity_record()["activities"][0]["impact_grid"]
    second["mtpd"] = "24 h"
    second["recovery_target"] = "8 h"
    base["activities"] = [first, second]
    return base


def test_one_asset_two_departments_different_clocks_is_flagged(monkeypatch):
    """W9. Willem: HR asks 110 % recovery seats, trading 5 % — 'doesn't make sense'. The
    machine-checkable form is a shared dependency whose consumers disagree on when it must
    be back. The strictest consumer binds."""
    _install(monkeypatch, _pack_with_clocks())
    out = validate_bia_record("marschkamp", _two_department_record())
    assert out["pass"] is False, out
    joined = " ".join(out["rejections"])
    assert "KA-01" in joined and "schlachtung" in joined and "zerlegung" in joined, out


def test_two_departments_agreeing_on_the_clock_pass(monkeypatch):
    """The guard against flagging every shared dependency."""
    _install(monkeypatch, _pack_with_clocks())
    rec = _two_department_record(tighten_first=False)
    out = validate_bia_record("marschkamp", rec)
    assert not [e for e in out.get("rejections", []) if "departments" in e], out


def test_one_department_twice_is_not_a_cross_department_conflict(monkeypatch):
    """Two activities of the SAME department may legitimately differ; this check is about
    departments disagreeing, not activities."""
    _install(monkeypatch, _pack_with_clocks())
    rec = _two_department_record()
    rec["activities"][1]["dept"] = "schlachtung"
    out = validate_bia_record("marschkamp", rec)
    assert not [e for e in out.get("rejections", []) if "departments" in e], out


def test_cross_department_ack_silences_only_with_a_named_human(monkeypatch):
    """The silencing branch, exercised — the same contract as shared_quote_ack and
    dependency_gap_flagged: name the assets AND carry a human's name, "sonst ist es
    ein klick" (Hans, 2026-08-20). A boolean is not the contract at all."""
    _install(monkeypatch, _pack_with_clocks())
    rec = _two_department_record()
    rec["cross_department_ack"] = {"assets": ["KA-01"], "approved_by": "W. Marschkamp"}
    out = validate_bia_record("marschkamp", rec)
    assert not [e for e in out.get("rejections", []) if "departments" in e], out
    rec["cross_department_ack"] = {"assets": ["KA-01"]}   # no name — still flagged
    out = validate_bia_record("marschkamp", rec)
    assert [e for e in out.get("rejections", []) if "departments" in e], out
    rec["cross_department_ack"] = True                     # a click — still flagged
    out = validate_bia_record("marschkamp", rec)
    assert [e for e in out.get("rejections", []) if "departments" in e], out

# ── accumulation: a later BIA must not drop an earlier one's activities ──────
# The dependency graph renders BIA activity cards from output/bia-record.json and
# nothing else. Before this, a second BIA saved a record containing only its own
# activity, the save replaced the file, and the first process silently vanished
# from the graph — observed live 2026-08-03 when Cutting overwrote Slaughter.
# The merge has to happen here rather than at write time: the PASS binds the
# record's canonical bytes to a save_token and the write is by reference, so
# merging later would write bytes the referee never validated.


def _named_activity_record(name: str) -> dict:
    rec = valid_activity_record()
    rec["activities"][0]["name"] = name
    return rec


def test_wrapper_preserves_previously_saved_activities(monkeypatch):
    """Second BIA, different activity: both survive, prior order first."""
    files = _pack()
    files["output/bia-record.json"] = json.dumps(_named_activity_record("Slaughter Process"))
    _install(monkeypatch, files)

    out = validate_bia_record("marschkamp", _named_activity_record("Cutting / deboning"))
    assert out["pass"] is True, out

    bound = json.loads(graph_files._validated_records["marschkamp"]["data"].decode("utf-8"))
    assert [a["name"] for a in bound["activities"]] == ["Slaughter Process", "Cutting / deboning"]


def test_wrapper_keeps_two_processes_of_one_department_apart(monkeypatch):
    """The department key must not collapse genuinely different activities: an explicit id wins."""
    files = _pack()
    prior = _named_activity_record("Stunning")
    prior["activities"][0].update({"department": "schlachtung", "id": "ACT-1"})
    files["output/bia-record.json"] = json.dumps(prior)
    _install(monkeypatch, files)

    incoming = _named_activity_record("Dressing")
    incoming["activities"][0].update({"department": "schlachtung", "id": "ACT-2"})
    out = validate_bia_record("marschkamp", incoming)
    assert out["pass"] is True, out

    bound = json.loads(graph_files._validated_records["marschkamp"]["data"].decode("utf-8"))
    assert [a["name"] for a in bound["activities"]] == ["Stunning", "Dressing"]


def test_wrapper_upserts_an_activity_of_the_same_name(monkeypatch):
    """Re-running the SAME process replaces its entry rather than duplicating it —
    otherwise a corrected re-run leaves two contradictory cards on the graph."""
    files = _pack()
    prior = _named_activity_record("Cutting / deboning")
    prior["activities"][0]["mtpd"] = "48 h"
    files["output/bia-record.json"] = json.dumps(prior)
    _install(monkeypatch, files)

    out = validate_bia_record("marschkamp", _named_activity_record("Cutting / deboning"))
    assert out["pass"] is True, out

    bound = json.loads(graph_files._validated_records["marschkamp"]["data"].decode("utf-8"))
    assert [a["name"] for a in bound["activities"]] == ["Cutting / deboning"]


def test_wrapper_leaves_the_first_bia_untouched(monkeypatch):
    """No saved record yet: nothing to preserve, the record passes through as written."""
    _install(monkeypatch, _pack())
    out = validate_bia_record("marschkamp", _named_activity_record("Slaughter Process"))
    assert out["pass"] is True, out

    bound = json.loads(graph_files._validated_records["marschkamp"]["data"].decode("utf-8"))
    assert [a["name"] for a in bound["activities"]] == ["Slaughter Process"]


def test_wrapper_refuses_when_the_saved_record_is_unreadable(monkeypatch):
    """A corrupt saved record must fail loudly. Passing the incoming record through
    would look like a normal save and destroy every activity already banked."""
    files = _pack()
    files["output/bia-record.json"] = "{not json"
    _install(monkeypatch, files)

    out = validate_bia_record("marschkamp", _named_activity_record("Cutting / deboning"))
    assert "error" in out, out
    assert "bia-record.json" in out["error"]


def test_wrapper_keys_activities_by_name_when_no_id_is_present(monkeypatch):
    """Live records carry no `id` — Slaughter and Cutting are both `id: null` and are
    told apart by name alone. Keying on id would merge them into a single card."""
    def _no_id(name: str) -> dict:
        rec = _named_activity_record(name)
        rec["activities"][0].pop("id", None)
        return rec

    files = _pack()
    files["output/bia-record.json"] = json.dumps(_no_id("Slaughter Process"))
    _install(monkeypatch, files)

    out = validate_bia_record("marschkamp", _no_id("Cutting / deboning"))
    assert out["pass"] is True, out
    bound = json.loads(graph_files._validated_records["marschkamp"]["data"].decode("utf-8"))
    assert [a["name"] for a in bound["activities"]] == ["Slaughter Process", "Cutting / deboning"]


# ── grandfathering: the lens rule judges a claim made now, not one already on disk ────
# The live marschkamp record was PASSed by the pre-rule referee at 2026-08-18T21:00:12Z, so
# its evidence carries no `lens` while financial is scored 2+. Every path that re-validates
# the saved record (a Stage 4 merge, an owner correction) feeds those activities back to the
# referee. An activity identical to its saved copy in `impact_grid` and `evidence` is not a
# new claim: the lens check skips it, every other check still runs.


def _untagged_activity_record(name: str = "Slaughter Process") -> dict:
    """The pre-rule live shape: financial scored 2+ with evidence carrying no `lens`."""
    rec = _named_activity_record(name)
    rec["activities"][0]["evidence"][0].pop("lens", None)
    return rec


def test_wrapper_grandfathers_an_unchanged_saved_activity_with_untagged_evidence(monkeypatch):
    files = _pack()
    files["output/bia-record.json"] = json.dumps(_untagged_activity_record())
    _install(monkeypatch, files)

    out = validate_bia_record("marschkamp", _untagged_activity_record())
    assert out["pass"] is True, out


def test_wrapper_re_litigates_a_saved_activity_whose_claim_changed(monkeypatch):
    """Grandfathered by identity, not provenance: touch the grid or the evidence and the
    activity is a claim made now, so the lens rule applies to it again."""
    files = _pack()
    files["output/bia-record.json"] = json.dumps(_untagged_activity_record())
    _install(monkeypatch, files)

    regraded = _untagged_activity_record()
    regraded["activities"][0]["impact_grid"]["customer-delivery"]["24 h"] = 3
    out = validate_bia_record("marschkamp", regraded)
    assert out["pass"] is False, out
    assert any("no evidence item carries lens" in e for e in out["rejections"]), out

    requoted = _untagged_activity_record()
    requoted["activities"][0]["evidence"][0]["quote"] = "we can be back in about eight hours"
    out = validate_bia_record("marschkamp", requoted)
    assert out["pass"] is False, out
    assert any("no evidence item carries lens 'financial'" in e for e in out["rejections"]), out


def test_wrapper_lens_rejection_names_only_the_newly_drafted_activity(monkeypatch):
    """A new activity with an untagged scored lens is rejected while the saved one beside it
    is grandfathered — the rejection must not name the activity nobody re-drafted."""
    files = _pack()
    files["output/bia-record.json"] = json.dumps(_untagged_activity_record("Slaughter Process"))
    _install(monkeypatch, files)

    incoming = _untagged_activity_record("Cutting / deboning")
    incoming["activities"][0]["id"] = "cutting"
    out = validate_bia_record("marschkamp", incoming)
    assert out["pass"] is False, out
    assert any("cutting-impact" in e and "no evidence item carries lens" in e
               for e in out["rejections"]), out
    assert not any("slaughter-impact" in e for e in out["rejections"]), out


def test_wrapper_ignores_a_caller_supplied_approved_before_flag(monkeypatch):
    """`approved_before` is minted by the referee from the saved copy and never accepted from
    the record: an agent that sets it cannot buy its way past the lens check."""
    _install(monkeypatch, _pack())
    rec = valid_record()
    rec["questions"][0]["evidence"][0].pop("lens")
    rec["questions"][0]["approved_before"] = True
    out = validate_bia_record("marschkamp", rec)
    assert out["pass"] is False, out
    assert any("no evidence item carries lens 'financial'" in e for e in out["rejections"]), out


# ── 2026-08-17: an interview held in chat is quotable ─────────────────────────────────
# W33 digest, Willem's 12.08 run: the agent interviewed him in chat, saved the transcript to
# output/owner-interviews/ (the only folder it may write), and Stage 4 then had no quote source
# because the referee read 07_Interviews only — four rejections and "what am I supposed to do?".

def test_wrapper_quotes_a_chat_interview_saved_under_output_owner_interviews(monkeypatch):
    files = _pack()
    del files["07_Interviews/2026-06-01-ahlgrim-interview.md"]
    files["output/owner-interviews/slaughter-interview-transcript.md"] = TRANSCRIPT
    _install(monkeypatch, files)
    rec = valid_activity_record()
    rec["activities"][0]["evidence"][0]["source_path"] = (
        "output/owner-interviews/slaughter-interview-transcript.md")
    out = validate_bia_record("marschkamp", rec)
    assert out["pass"] is True, out


def test_referee_does_not_quote_the_agents_own_stage_artifacts(monkeypatch):
    """D-14, run (b) 2026-08-18. output/owner-interviews/ is a quote source (bia_referee:365) and
    :599 folded in every direct child unfiltered, so at the first referee round (20:57:06Z) the
    anti-fabrication check was reading Bruno's own stage 1, 2 and 3 next to the real transcript.
    Hans: "if the referee cannot tell my transcript from brunos own three files in the same
    folder, then the thing guarding my belief was me, not the check." The agent's stage artifacts
    are not interview evidence — same derived set the write jaw refuses."""
    files = _pack()
    del files["07_Interviews/2026-06-01-ahlgrim-interview.md"]
    files["output/owner-interviews/stage2-interview-capture.md"] = TRANSCRIPT
    _install(monkeypatch, files)
    rec = valid_activity_record()
    rec["activities"][0]["evidence"][0]["source_path"] = (
        "output/owner-interviews/stage2-interview-capture.md")
    out = validate_bia_record("marschkamp", rec)
    assert out["pass"] is False, "the agent's own stage 2 capture must not be quotable evidence"


def test_another_bias_chat_interview_is_not_a_quote_source_for_this_one(monkeypatch):
    """Leo's audit 2026-08-19, objection 5: D-14 (`2f65f62`) dropped the agent's own stage
    artifacts from the blob the exact-quote check reads, but `output/owner-interviews/` is shared
    by every BIA, and the other BIAs' transcripts stayed in it — today the folder still holds
    `onboarding-interview-transcript.md` and `lf-abp-01-owner-interview.md`. Neither is a legacy
    singleton, so a Slaughter activity could satisfy the anti-fabrication check against the HR
    onboarding interview. A chat transcript is evidence for the BIA that cites it by name: it
    stays resolvable through `source_path`, and it is no longer folded into the anonymous blob."""
    files = _pack()
    del files["07_Interviews/2026-06-01-ahlgrim-interview.md"]
    files["output/owner-interviews/onboarding-interview-transcript.md"] = TRANSCRIPT
    _install(monkeypatch, files)
    rec = valid_activity_record()          # quotes TRANSCRIPT, cites no source_path
    rec["activities"][0]["evidence"][0].pop("source_path", None)
    out = validate_bia_record("marschkamp", rec)
    assert out["pass"] is False, "another BIA's chat transcript must not validate this quote"
    assert any("source_path" in r for r in out["rejections"]), out["rejections"]


def test_wrapper_still_rejects_a_quote_missing_from_every_transcript_folder(monkeypatch):
    files = _pack()
    files["output/owner-interviews/slaughter-interview-transcript.md"] = "Nothing quotable here."
    _install(monkeypatch, files)
    rec = valid_activity_record()
    rec["activities"][0]["evidence"][0]["quote"] = "a sentence nobody said"
    out = validate_bia_record("marschkamp", rec)
    assert out["pass"] is False


# ── pp4_missing: the PP4-handoff write jaw's enumeration rule (moved here from the retired
#    offline grader bia_verify.py, B3 2026-08-18; graph_files.write_file calls it) ─────────
def test_pp4_missing_flags_dropped_register_item():
    """Ledger #11 / lesson #26: PE-ZERLEG-01 dropped from the handoff three times running."""
    register = {
        "LF-ABP-01": {"asset_id": "LF-ABP-01", "owner_name": "Dr Katrin Sauer", "pp4_issue": True},
        "PE-ZERLEG-01": {"asset_id": "PE-ZERLEG-01", "owner_name": "Petra Louven", "pp4_issue": True},
        "KA-01": {"asset_id": "KA-01", "owner_name": "Dirk Wohlleben"},
    }
    handoff = "Open items: LF-ABP-01 backup renderer contract gap remains open.\n"
    assert bia_referee.pp4_missing(register, handoff) == ["PE-ZERLEG-01"]


def test_pp4_missing_tolerates_typographic_hyphens():
    """Copilot renders ids with non-breaking hyphens (U+2011) — still counts as enumerated."""
    register = {"PE-ZERLEG-01": {"asset_id": "PE-ZERLEG-01", "owner_name": "P", "pp4_issue": True}}
    handoff = "Register-wide: PE‑ZERLEG‑01 labour pool carry-forward.\n"
    assert bia_referee.pp4_missing(register, handoff) == []
