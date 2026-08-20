"""Journey payload/artifact contract — learnings from the 2026-07-21 acceptance run.

Separate file (not test_smoke.py) so a parallel session's uncommitted smoke-test work
is never co-staged. Covers: (1) the stage payload carries its own literal advance call —
Copilot's orchestrator slot-fills next_step's stage_id from visible context, and the run
stalled twice on "Please provide the stage_id" because the id lived only in a prior turn's
tool result; the literal-call pattern is already proven by the stage-4 reality loop.
(2) run-bia.yaml pins the canonical artifact filenames the offline grader reads — the run's
agent invented its own names and needed a live correction.
(3) P7 I-1 stage binding: journey-owned document_contracts (server-enforced markers +
floors per stage) and the next_step advance gate that closes the referent-substitution
hole (halfB 19:40:02, 367 B stub PUT).
"""
import ast
import inspect
import json
from pathlib import Path

import pytest

import addendum_tools
import graph_files
import journeys as journey_engine

YAML = (Path(__file__).resolve().parent / "design" / "run-bia.yaml").read_text("utf-8")

# Captured at import time — BEFORE conftest's autouse world stub patches the module
# attribute — so the delegation test below exercises the REAL seam, not a test double.
_REAL_FETCH = addendum_tools._fetch_artifact


def _bia():
    return journey_engine.load_journeys()["run-bia"]


def _instructions(payload) -> str:
    """Everything the model reads as instruction: rules, voice, worked examples.
    A lesson may MOVE between them (2026-08-19). It may not leave."""
    return " ".join(json.dumps(payload[k], ensure_ascii=False)
                    for k in ("protocol", "voice", "examples") if k in payload)


def test_stage_payload_carries_advance_call():
    bia = _bia()
    total = len(bia.stages)
    for n, stage in enumerate(bia.stages, start=1):
        payload = journey_engine.render_stage_tool(bia, stage, n, total)
        # per-BIA folders (owner ruling 2026-08-18): the literal call names the folder argument too
        assert f"next_step('run-bia', '{stage.id}', bia='<bia>')" in payload["advance"], stage.id
        assert "never ask the user" in payload["advance"]


def test_yaml_pins_canonical_artifact_paths():
    # graph_files hard-codes these paths; the journey must name them.
    draft_stage = YAML.split("id: draft-and-review")[1].split("id: solution-design")[0]
    for path in ("output/bia-record.json", "output/<bia>/bia-draft.md", "output/<bia>/bia-signoff.json"):
        assert path in draft_stage, path
    solution_stage = YAML.split("id: solution-design")[1]
    assert "output/<bia>/pp4-handoff.md" in solution_stage
    # the shared machine record is the ONE flat artifact left — the graph renders from it and it merges
    for gone in ("output/bia-draft.md", "output/bia-signoff.json", "output/pp4-handoff.md",
                 "output/stage1-scope-and-guide.md", "output/stage2-interview-capture.md",
                 "output/stage3-dependency-analysis.md"):
        assert gone not in YAML, gone


# ── P7 I-1 part 1a: journey-owned artifact contracts ─────────────────────────────────

def test_stage_parses_document_contracts_and_defaults_empty():
    s = journey_engine._stage_from({
        "id": "s", "goal": "g",
        "document_contracts": [{"path": "output/a.md", "markers": ["## A"], "min_bytes": 10}],
    })
    assert s.document_contracts[0]["path"] == "output/a.md"
    assert journey_engine._stage_from({"id": "s2", "goal": "g"}).document_contracts == []


def _journey_with(contract):
    s = journey_engine.Stage(id="s1", goal="g", document_contracts=[contract])
    return journey_engine.Journey(id="j", persona="p", title="t", when_to_use="w", stages=[s])


@pytest.mark.parametrize("bad", [
    "not-a-dict",
    {"path": "output/x.md", "markers": [], "min_bytes": 100, "bogus": 1},
    {"markers": [], "min_bytes": 100},                        # path missing
    {"path": "", "markers": [], "min_bytes": 100},
    {"path": "notes/x.md", "markers": [], "min_bytes": 100},  # outside output/
    {"path": "output/x.md", "markers": "## A", "min_bytes": 100},
    {"path": "output/x.md", "markers": ["ok", ""], "min_bytes": 100},
    {"path": "output/x.md", "markers": [], "min_bytes": 0},
    {"path": "output/x.md", "markers": [], "min_bytes": True},
    {"path": "output/x.md", "markers": [], "min_bytes": 100, "name": ""},
])
def test_validate_journey_rejects_malformed_contracts(bad):
    with pytest.raises(ValueError):
        journey_engine.validate_journey(_journey_with(bad), None)


# KG §6 decision 2+4 (2026-07-26): canonical paths pinned for stages 1-3, patterns for the
# owner side-quest, the three stage-5 paths, pp4-handoff — with the LOW-by-design floors
# (markers carry the contract; floors only kill headline-class stubs like the 367 B one).
CANON = {
    "scope-and-risk": [("output/<bia>/stage1-scope-and-guide.md", 1200)],
    "capture-transcript": [("output/<bia>/stage2-interview-capture.md", 1200)],
    "analyse-transcript": [("output/<bia>/stage3-dependency-analysis.md", 1200)],
    "asset-owner-capture": [("output/owner-interviews/*.md", 800),
                            ("output/proposals/*-owner-capture.md", 800)],
    "draft-and-review": [("output/bia-record.json", 1500), ("output/<bia>/bia-draft.md", 1200),
                         ("output/<bia>/bia-signoff.json", 200)],
    "solution-design": [("output/<bia>/pp4-handoff.md", 1200)],
}


def test_run_bia_pins_canonical_document_contracts():
    bia = _bia()
    for stage in bia.stages:
        got = [(c["path"], c["min_bytes"]) for c in stage.document_contracts]
        assert got == CANON[stage.id], stage.id
    stage2 = bia.stage("capture-transcript").document_contracts[0]
    assert stage2["markers"] == ["## Impacts", "## Dependencies", "## Assumptions",
                                 "## Unresolved points", "## Gaps"]


def test_render_stage_tool_carries_contract_block():
    bia = _bia()
    s2 = bia.stage("capture-transcript")
    payload = journey_engine.render_stage_tool(bia, s2, 2, 6)
    assert payload["document_contracts"] == s2.document_contracts  # single source, no drift
    plan = journey_engine.load_journeys()["draft-plan"]
    p1 = plan.first_stage()
    assert "document_contracts" not in journey_engine.render_stage_tool(
        plan, p1, 1, len(plan.stages))


def test_render_stage_prompt_names_stage_artifact():
    bia = _bia()
    text = journey_engine.render_stage_prompt(bia, bia.first_stage(), 1, 6)
    assert "output/<bia>/stage1-scope-and-guide.md" in text


def test_stage4_pins_the_activity_name_to_the_saved_record():
    """Run (a), 2026-08-18: the morning run wrote "Slaughter process (stunning → dressing)", the
    evening one "Slaughter Process" — the shared record keys activities by name, so the graph grew a
    second Slaughter card and Hans refused to sign it off ("i wont leave two of them lying in there").
    The record has no removal or rename path by design (update_bia_activity: the name is analysis),
    so the only cure is prevention: reuse the name already saved for that department."""
    stage4 = YAML.split("id: draft-and-review")[1].split("id: solution-design")[0]
    flat = " ".join(stage4.split())
    assert "output/bia-record.json" in flat
    assert "reuse the exact activity name already saved" in flat
    assert "never a second card for the same process" in flat


def test_stage1_prompt_defines_the_bia_folder():
    """Owner ruling 2026-08-18: runs ADD to output/, they never overwrite another BIA. Stage 1
    names the folder once — the process slug — and the same-process rule (reuse, approval-gated)."""
    stage1 = YAML.split("id: scope-and-risk")[1].split("id: capture-transcript")[0]
    flat = " ".join(stage1.split())
    assert "output/<bia>/" in flat
    assert "output/slaughter/" in flat          # a worked example the model can copy
    assert "lowercase" in flat and "hyphen" in flat
    assert "reuses its folder" in flat            # same process → same folder, approval-gated
    assert "never" in flat and "another BIA" in flat


# ── P7 I-1 part 4: run-bia.yaml wording carries the binding, not a license ───────────

def test_stage2_prompt_names_canonical_path_and_kills_the_summary_license():
    stage2 = YAML.split("id: capture-transcript")[1].split("id: analyse-transcript")[0]
    assert "output/<bia>/stage2-interview-capture.md" in stage2
    flat = " ".join(stage2.split())
    assert "COMPLETE structured capture" in flat
    assert "applies only to additional files" in flat  # "keep it short" ≠ stub the artifact


def test_stage5_capture_is_derived_material_and_record_saves_by_token():
    stage5 = YAML.split("id: draft-and-review")[1].split("id: solution-design")[0]
    flat = " ".join(stage5.split())
    assert "derived material and never the quote source" in flat
    assert "is a summary" not in flat  # the wording a substitution reading leaned on
    assert "save_token" in flat and "never re-type the record" in flat


# ── 2026-07-30 contract bundle: the journey elicits register-asset dependencies ──────


def test_stage3_names_dependencies_by_exact_register_asset_id():
    stage3 = YAML.split("id: analyse-transcript")[1].split("id: asset-owner-capture")[0]
    flat = " ".join(stage3.split())
    assert "exact register asset id" in flat
    assert "finding, not" in flat  # an unmodeled dep stays a finding, never an invented id


def test_stage5_record_contract_requires_the_dependencies_list():
    """REVERSAL, 2026-07-31: the stage used to say "omit the field when none apply",
    which the referee then honoured by treating an absent list as a pass. marschkamp's
    Slaughter Process was saved with no linkage under exactly that pair and surfaced as
    an edgeless card on the dependency graph. Both halves moved together — pin the
    instruction here and the rejection in test_bia_referee, or the journey tells the
    drafter to do something the gate rejects."""
    stage5 = YAML.split("id: draft-and-review")[1].split("id: solution-design")[0]
    flat = " ".join(stage5.split())
    assert "dependencies list of exact register asset ids" in flat
    assert "required, never omitted and never empty" in flat
    assert "omit the field when none apply" not in flat
    # `dept` is the register join key and is gated in the same loop as `dependencies`;
    # the instruction has to name it or the drafter gets rejected for a field it was
    # never asked for — the failure mode this whole contract exists to prevent.
    assert "dept naming the department that performs it" in flat
    assert "SAME dept value the dependency register uses" in flat


# ── P7 I-1 part 2: stage-advance artifact gate (also the unbuilt I-5 catch) ──────────

STAGE2 = "output/slaughter/stage2-interview-capture.md"  # <bia> substituted by next_step(bia=)


def _world(monkeypatch, files):
    """Fake company data source for the gate: {relative path: content}. Returns call log."""
    calls = []

    def fetch(company, path):
        calls.append((company, path))
        if path in files:
            return {"path": f"{company}/{path}", "content": files[path],
                    "size": len(files[path].encode("utf-8"))}
        return {"error": f"file not found: {company}/{path}"}

    # raising=False: during red-phase the seam does not exist yet — the test must FAIL
    # on the missing gate, not ERROR on the missing attribute.
    monkeypatch.setattr(addendum_tools, "_fetch_artifact", fetch, raising=False)
    return calls


def _conforming(stage_id, idx=0):
    c = _bia().stage(stage_id).document_contracts[idx]
    return "\n".join(c["markers"]) + "\n" + "x" * c["min_bytes"]


def test_advance_blocked_when_stage_artifact_missing(monkeypatch):
    _world(monkeypatch, {})
    out = addendum_tools.next_step_fn("run-bia", "capture-transcript", bia="slaughter")
    assert out.get("error") == "stage_incomplete"
    msg = out["message"]
    assert "Stage 2" in msg and STAGE2 in msg
    assert "hasn't been saved" in msg
    assert "then advance" in msg


def test_advance_blocked_when_artifact_is_a_stub(monkeypatch):
    thin = "# Headline only\n\n## Impacts\nMTPD 24h.\n"
    _world(monkeypatch, {STAGE2: thin})
    out = addendum_tools.next_step_fn("run-bia", "capture-transcript", bia="slaughter")
    assert out.get("error") == "stage_incomplete"
    msg = out["message"]
    assert "Stage 2" in msg and str(len(thin.encode("utf-8"))) in msg
    assert "## Dependencies" in msg  # names what is missing, in the teaching voice
    assert "Save the full" in msg


def test_advance_proceeds_when_contract_met(monkeypatch):
    calls = _world(monkeypatch, {STAGE2: _conforming("capture-transcript")})
    out = addendum_tools.next_step_fn("run-bia", "capture-transcript", bia="slaughter")
    assert out.get("stage_id") == "analyse-transcript"
    assert calls == [("marschkamp", STAGE2)]  # completed stage checked, company defaulted


def test_advance_gate_forwards_explicit_company(monkeypatch):
    calls = _world(monkeypatch, {})
    out = addendum_tools.next_step_fn("run-bia", "capture-transcript",
                                      company="marschkamp-demo", bia="slaughter")
    assert out.get("error") == "stage_incomplete"
    assert calls[0][0] == "marschkamp-demo"


def test_advance_needs_the_bia_folder_for_per_bia_contracts(monkeypatch):
    """Owner ruling 2026-08-18: the six BIA documents live in output/<bia>/ — without the folder the
    gate cannot know which BIA it is proving, so it refuses (no SharePoint read) and says how to call."""
    calls = _world(monkeypatch, {STAGE2: _conforming("capture-transcript")})
    out = addendum_tools.next_step_fn("run-bia", "capture-transcript")
    assert out.get("error") == "stage_incomplete"
    assert "bia=" in out["message"] and "output/<bia>/" in out["message"]
    assert "bia=" in out["next_move"]
    assert calls == []


def test_advance_refuses_a_non_slug_bia_folder(monkeypatch):
    calls = _world(monkeypatch, {})
    out = addendum_tools.next_step_fn("run-bia", "capture-transcript", bia="Slaughter Process")
    assert out.get("error") == "stage_incomplete"
    assert "lowercase" in out["message"] and "slaughter-process" in out["message"]
    assert calls == []


def test_numeric_resume_bypasses_gate_by_design(monkeypatch):
    """Documented residual: resume ≠ advance — the gate is anti-softening, not
    anti-adversarial-manager. A human 'Stage 3' resume never reads SharePoint."""
    calls = _world(monkeypatch, {})
    out = addendum_tools.next_step_fn("run-bia", "Stage 3")
    assert out.get("resumed") is True and calls == []


def test_stage4_pattern_contracts_not_advance_gated(monkeypatch):
    """The owner side-quest's N/A branch is register-dependent — patterns are
    write-time contracts only; the 4→5 advance must not require them."""
    calls = _world(monkeypatch, {})
    out = addendum_tools.next_step_fn("run-bia", "asset-owner-capture")
    assert out.get("stage_id") == "draft-and-review"
    assert calls == []


def test_done_path_gated_on_final_stage_artifact(monkeypatch):
    _world(monkeypatch, {})
    out = addendum_tools.next_step_fn("run-bia", "solution-design", bia="slaughter")
    assert out.get("error") == "stage_incomplete"
    assert "output/slaughter/pp4-handoff.md" in out["message"]
    ok = _world(monkeypatch, {"output/slaughter/pp4-handoff.md": _conforming("solution-design")})
    done = addendum_tools.next_step_fn("run-bia", "solution-design", bia="slaughter")
    assert done.get("done") is True
    assert ok == [("marschkamp", "output/slaughter/pp4-handoff.md")]


def test_gate_reports_unreadable_company_as_unverifiable_not_unsaved(monkeypatch):
    """An unknown/unreadable company is a verification failure, not a missing artifact —
    the teaching must not tell the manager to re-save into a folder that can't be read."""
    def fetch(company, path):
        return {"error": f"unknown company '{company}' — allowed: marschkamp"}

    monkeypatch.setattr(addendum_tools, "_fetch_artifact", fetch, raising=False)
    out = addendum_tools.next_step_fn("run-bia", "capture-transcript", company="typo-co")
    assert out.get("error") == "stage_incomplete"
    assert "cannot be verified" in out["message"]
    assert "hasn't been saved" not in out["message"]


def test_gate_fails_closed_and_legible_on_data_source_outage(monkeypatch):
    import httpx

    def boom(company, path):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(addendum_tools, "_fetch_artifact", boom, raising=False)
    out = addendum_tools.next_step_fn("run-bia", "capture-transcript")
    assert out.get("error") == "stage_incomplete"
    assert "cannot be verified" in out["message"]
    assert "no route" not in out["message"]  # legible, no internals


def test_fetch_artifact_seam_delegates_to_graph_read(monkeypatch):
    """The gate's only live-Graph line: every other test stubs the seam, so pin that it
    forwards (company, path) — in that order — to graph_files.read_file. A rename or arg
    swap there would otherwise ship green on stubs and only fail against live SharePoint."""
    calls = []
    monkeypatch.setattr(graph_files, "read_file",
                        lambda c, p: calls.append((c, p)) or {"content": "x"})
    assert _REAL_FETCH("marschkamp", "output/a.md") == {"content": "x"}
    assert calls == [("marschkamp", "output/a.md")]


# --- 2026-08-16: the five traditional stage names (Willem, 13.08) -----------------------
# 2026-08-19: this test's ban on any n/total field (below, in the smoke suite) reverses here
# too. The reason was right — a fraction over all six *entries* contradicts the deck's five —
# the remedy wasn't: banning the fraction outright left no way to say "Stage 1 of 5" at all,
# which Bruno needs. `card` now derives the denominator from the stages whose own label is a
# plain integer (five; 3a is excluded by derivation, not a hand-typed 5) and stays bare (no
# "of") on 3a itself. The `<name>` / `<n>/<total>` protocol needles move out: a later task
# rewrites STAGE_PROTOCOL without `<name>`, so they belong to that task, not this one.

def test_run_bia_stage_names_are_the_traditional_five():
    """KG fixed the five names with Willem on 13.08.2026; the owner loop is '3a' so the agent
    and the BC-Consulting deck agree on five. The number lives inside the name because the
    journey has six stages and a card saying 'Stage 4/6' would contradict the deck."""
    bia = _bia()
    assert [s.name for s in bia.stages] == [
        "Stage 1 · Identification of scope",
        "Stage 2 · Structured interview (conversational)",
        "Stage 3 · Convert the interview to the standardised template",
        "Stage 3a · Missing-owner loop (only when a dependency has no owner)",
        "Stage 4 · List the requirements (RTO, MTPD, RPO — the numbers)",
        "Stage 5 · Consolidate requirements + sanity check → handover",
    ]
    payload = journey_engine.render_stage_tool(bia, bia.first_stage(), 1, 6)
    assert payload["name"] == "Stage 1 · Identification of scope"
    assert payload["card"] == "**Stage 1 of 5 · Identification of scope**"
    owner_loop = bia.stage("asset-owner-capture")
    card_3a = journey_engine.render_stage_tool(bia, owner_loop, 4, 6)["card"]
    assert card_3a == f"**{owner_loop.name}**"
    assert " of " not in card_3a
    text = journey_engine.render_stage_prompt(bia, bia.first_stage(), 1, 6)
    assert text.startswith("# Run a BIA end-to-end — Stage 1 · Identification of scope:")


def test_bold_is_the_only_difference_between_the_card_text_and_the_card():
    """Hans's limit, 2026-08-19: "one bold line is a header — it says where i am, thats a fact
    not an instruction… the risk is he doesnt stop at one: bold the stage, then the process
    name, then the RTO, and its a generated document again. one line and nothing else."
    `_card_text` derives, `_card_label` presents, and presentation is exactly two asterisks on
    each side — so a later session cannot quietly add a second piece of formatting here."""
    bia = _bia()
    for n, stage in enumerate(bia.stages, start=1):
        plain = journey_engine._card_text(bia, stage, n, len(bia.stages))
        assert journey_engine._card_label(bia, stage, n, len(bia.stages)) == f"**{plain}**"
        assert "**" not in plain


def test_card_label_falls_back_for_a_journey_with_no_stage_names():
    """draft-plan's stages carry no `name` field at all (unlike run-bia's) — `_card_label`
    must not crash on a label-free journey. With no name to derive a label from, it falls
    back to the stage's own name (empty here) and then to a plain position fraction."""
    plan = journey_engine.load_journeys()["draft-plan"]
    s1 = plan.first_stage()
    assert s1.name == ""
    assert journey_engine._card_text(plan, s1, 1, len(plan.stages)) == \
        f"Stage 1 of {len(plan.stages)}"


def test_run_bia_prose_has_no_agent_jargon_and_carries_the_13_08_learnings():
    """Willem, 13.08: 'no normal human being knows what you mean with an artifact'; stage 1 is
    identification (department metadata + key activities), method parameters are preconditions,
    stage 2 is a conducted interview (process-first, urgency not importance, workaround per
    resource), stage 5 sanity-checks and hands over. Machine keys (document_contracts, markers)
    are deliberately not covered — only prose."""
    bia = _bia()
    prose = " ".join(
        " ".join([s.goal, s.copy_paste_prompt, s.connector_guidance, s.approval_gate,
                  s.expected_output, " ".join(s.reviewer_checklist), " ".join(s.questionnaire)])
        for s in bia.stages)
    assert "artifact" not in prose.lower(), "say 'document' or 'saved file' — Willem, 13.08"
    s1, s2, s5 = bia.stage("scope-and-risk"), bia.stage("capture-transcript"), bia.stage("solution-design")
    assert "head of the department" in s1.copy_paste_prompt and "key activities" in s1.copy_paste_prompt
    assert "Preconditions" in s1.copy_paste_prompt
    assert "whatever the cause" in s2.copy_paste_prompt and "workaround" in s2.copy_paste_prompt
    assert "urgency, not importance" in s2.copy_paste_prompt
    assert "sanity check" in s5.copy_paste_prompt.lower()
    assert "PP4" not in s5.name


def test_bia_template_skeleton_has_willems_sections():
    """Stage 3 'convert the interview to the standardised template' names journeys/bia-template.json —
    the field list Willem described on 13.08 (department tab, activity tab: impact over time,
    resource requirements in four classes, dependencies on other departments, applications with
    RTO/RPO/workaround). His anonymised Excel confirms it; it does not unblock it."""
    import json
    t = json.loads((Path(journey_engine.JOURNEYS_DIR) / "bia-template.json").read_text("utf-8"))
    assert set(t) >= {"department", "activities", "department_rollup", "consolidation_notes", "_rules"}
    d = t["department"]
    assert {"legal_entity", "sub_unit", "headcount", "head", "bc_coordinator", "validation"} <= set(d)
    a = t["activities"][0]
    assert set(a) >= {"name", "related_process", "product_service", "owner", "critical_time_periods",
                      "impact_over_time", "mtpd", "priority_tier", "recovery_options_suitable",
                      "skeleton_staff", "resource_requirements", "dependencies_other_departments", "evidence"}
    assert set(a["resource_requirements"]) == {"people", "buildings_seats", "it_applications", "suppliers",
                                               "other_requirements"}
    app = a["resource_requirements"]["it_applications"][0]
    assert {"name_normalised", "instance", "rto", "rpo", "workaround", "it_actual_rto", "it_actual_rpo"} <= set(app)
    assert {"work_from_home", "work_transference", "alternative_site_seat", "staff_relocation",
            "manual_workaround"} == set(a["recovery_options_suitable"])
    dep = a["dependencies_other_departments"][0]
    assert {"department", "contact", "direction", "what", "rto", "alternative"} <= set(dep)
    # anonymised: concept only — no company or person names may travel with the template
    raw = json.dumps(t).lower()
    assert "nomura" not in raw and "continuity group" not in raw
    # C3 of the 2026-08-17 ponytail-audit: `journeys/bia-template.json` is a repo path no tool can
    # read (read_company_file reads the room, fetch reads KB chunks) — the agent filled the template
    # from the prompt's own description. Stage 3 names the room copy and the tool that reads it.
    s3 = _bia().stage("analyse-transcript").copy_paste_prompt
    assert "02_BCM-Method/bia-template.json" in s3 and "read_company_file" in s3
    assert "journeys/bia-template.json" not in s3


def test_stage_protocol_never_waits_for_continue_and_offers_options():
    """Willem, 13.08: 'why do I have to write continue?' and 'if you ask, give me the options'."""
    p = journey_engine.STAGE_PROTOCOL
    assert "never wait for the word 'continue'" in p.lower() or "Never wait for the word 'continue'" in p
    assert "list the options" in p


# --- 2026-08-16: smart next steps ---------------------------------------------------------

def test_every_stage_offers_next_moves_and_the_turn_ends_on_them():
    """Every turn ends on a next move picked from the stage's own list — the model picks, it
    does not invent (KG, 16.08: 'crucial for user-experience').

    2026-08-19, owner ruling after the acceptance run: the LOGIC of 'Next:' is kept whole —
    same predicate, same next_moves, never a dead end — but the literal label and the numbered
    list are retired. 'Next' reads as machinery; a colleague says what happens next in a
    sentence. The label is now banned outright, so the earlier 'on a stage-work turn, end with
    Next:' needle inverts.

    'Answerable in one word or one number' used to be asserted here as what stops a prose
    ending becoming a dead end. It is now asserted ABSENT: it contradicted the number rule two
    sentences above it, and the agent took the looser reading — a live Teams card on 2026-08-19
    closed with Say "yes" ... or "amend", words rather than digits. Hans's ruling replaced both
    with one rule that carries the number AND the word, so there is nothing left to contradict."""
    bia = _bia()
    for s in bia.stages:
        assert len(s.next_moves) >= 2, s.id
        assert all({"when", "offer"} <= set(m) for m in s.next_moves), s.id
    payload = journey_engine.render_stage_tool(bia, bia.first_stage(), 1, 6)
    assert payload["next_moves"] == bia.first_stage().next_moves
    p = journey_engine.STAGE_PROTOCOL
    assert "Next:" not in p                               # the label is retired, both forms
    assert "labelled list" in p and "in your own words" in p
    assert "at most two alternatives" in p and "next_moves" in p
    # 2026-08-19, second correction: dropping 'A gate is a number' with the label took the
    # one-digit answer with it. A choice between ACTIONS (draft / amend / jump ahead) is not a
    # company-data value, so the surviving 'list the options' rule never fires on it and the
    # manager got three options he had to answer in a sentence. The number comes back INSIDE
    # the prose, which is the owner's ruling, not a return to the bullet list.
    # Hans 2026-08-19: the number is not enough on its own — the word rides with it, so the
    # thread still reads next week instead of being a column of bare 1s.
    # Hans corrected the RENDERING on 2026-08-19 after seeing it live: dash not equals sign,
    # one option per line, and the closing last in the turn rather than behind a paragraph.
    assert "each on its own line, numbered, the word after a dash" in p
    assert "1 yes — use this scope" in p and "Never an equals sign" in p
    assert "it comes last, after everything else" in p
    assert "one word or one number" not in p                # the clause that contradicted it
    assert "a sentence or two" not in p                   # read as a cap on the whole turn
    # lower-cased needle would miss it now that the rule opens its own sentence rather than
    # trailing "and never…" — match the rule, not the sentence it happens to sit in.
    assert "make the user type a phrase back" in p


def test_gate_error_tells_the_next_move(monkeypatch):
    """A refused advance says what to do, not only what is wrong — `next_move`, never `next`
    (that key is the next stage id in stage payloads)."""
    _world(monkeypatch, {})
    out = addendum_tools.next_step_fn("run-bia", "capture-transcript", bia="slaughter")
    assert out["error"] == "stage_incomplete"
    assert out["next_move"].startswith("Write output/slaughter/stage2-interview-capture.md with sections")
    assert "## Impacts" in out["next_move"]


# --- 2026-08-17: the W33 usage-digest lessons (what 81 conversations actually typed) --------

def test_run_bia_carries_the_w33_digest_lessons():
    """bia-usage-digest-2026-w33: (1) seven prompts asked which process is most critical /
    where to start; (2) five asked for "the questionnaire" or the interview questions and five
    more asked for a questionnaire outside any journey; (3) Willem's 12.08 run asked to be
    interviewed one question at a time and needed a transcript file — his in-chat interview
    left Stage 4 with no quote source; (4) that run then stalled on four all-mechanical
    referee rejections and ended in "what am I supposed to do?"."""
    bia = _bia()
    assert "questionnaire" in bia.when_to_use  # off-journey questionnaire asks route here
    s1 = bia.stage("scope-and-risk")
    flat1 = " ".join(s1.copy_paste_prompt.split())
    assert "most critical" in flat1 and "never invent criticality" in flat1
    assert "'Questionnaire'" in flat1 and "pre-interview request" in flat1
    assert "Good Practice Guidelines" in flat1  # "what standard are you using?"
    offers1 = " ".join(m["offer"] for m in s1.next_moves)
    assert "Rank the recorded activities" in offers1 and "interview guide" in offers1
    s2 = bia.stage("capture-transcript")
    flat2 = " ".join(s2.copy_paste_prompt.split())
    assert "one question per turn" in flat2
    assert "output/owner-interviews/" in flat2  # the in-chat transcript is saved, then quoted
    s4 = bia.stage("draft-and-review")
    flat4 = " ".join(s4.copy_paste_prompt.split())
    assert "output/owner-interviews/" in flat4 and "derived material and never the quote source" in flat4
    assert "rejection text names the fix" in flat4
    assert "never a stop" in flat4  # unmodelled dependencies are a finding, the BIA continues


# ── run (a) 2026-08-18 merge loop: Hans's report §2 and §5 (wording, role-bia-facilitation) ────


def _stage(text_from: str, text_to: str) -> str:
    return " ".join(YAML.split(text_from)[1].split(text_to)[0].split())


def test_stage3a_existing_proposal_overwrites_in_place_and_never_halts():
    """19:34: 'Next: 1 remain halted until ownership of the shared folder is clarified · 2 create a
    process-local review copy only; that will not satisfy the Stage 3a contract' — two dead ends;
    Hans had to invent snapshot-then-overwrite. The live move: overwrite in place, version history
    keeps the earlier one; unclear provenance is a finding, not a stop."""
    s3a = _stage("id: asset-owner-capture", "id: draft-and-review")
    assert "overwrite it in place" in s3a and "version history" in s3a
    assert "never a halt" in s3a and "finding" in s3a
    moves = next(s for s in _bia().stages if s.id == "asset-owner-capture").next_moves
    assert any("already exists" in m["when"] for m in moves)


def test_stage3a_one_field_one_gate():
    """19:40, Hans: 'that was five steps for one field' — proposal overwrite, snapshot+overwrite,
    register diff, approval record, apply. One card (proposal sections + exact diff + the approval
    record it will carry), one named sign-off, then both writes without asking again."""
    s3a = _stage("id: asset-owner-capture", "id: draft-and-review")
    assert "One field, one gate" in s3a
    assert "sign-off once" in s3a and "Do not ask again" in s3a
    moves = next(s for s in _bia().stages if s.id == "asset-owner-capture").next_moves
    assert any("no second gate" in m["offer"] for m in moves)


def test_stage2_walks_the_declared_impact_categories_before_closing():
    """run (b) 2026-08-18, 20:46:25Z: Bruno's Stage 1 card named seven impact categories and the
    whole Sales interview closed without Financial ever being asked. Hans caught it, not the
    workflow — "you have financial in your seven categories and nobody asked me for a euro figure
    in the whole interview". ISO/TS 22317 5.6.1: 'complete' is one of the five checks, and nothing
    was running it."""
    s2 = _stage("id: capture-transcript", "id: analyse-transcript")
    assert "name each impact category" in s2
    assert "not asked" in s2


def test_stage4_never_scores_a_category_the_interview_did_not_cover():
    """Hans's one explicitly non-wording item: Stage 3 said "I will keep unsupported category
    scores open, not fill them by inference", then Stage 4 handed over a Financial row scored 3
    across five horizons off no euro figure. Fixed in one turn when quoted back at himself — "it
    should not need me". The existing MISSING rule was rationalised past because a criteria mapping
    can always be argued; binding the score to what the interview actually covered cannot."""
    s4 = _stage("id: draft-and-review", "id: solution-design")
    assert "a category the capture records as not asked is never scored" in s4
    assert "lens-tagged quote is rejected" in s4


def test_stage3_card_leads_with_the_conflicts_not_the_provenance():
    """Hans's condition for using it at all is turn length, and the Stage 3 preview was his near
    miss: "a screen and a half opening with **Numbers and provenance** and nine bullet quotes
    before it said anything I could act on ... on a phone in a corridor I stop reading there". The
    next turn came back as two conflict lines and a filename — "that is the shape the whole run
    should have had"."""
    s3 = _stage("id: analyse-transcript", "id: asset-owner-capture")
    assert "lead the card with the conflicts" in s3
    assert "provenance stays in the file" in s3


def test_previews_are_cards_not_pasted_documents():
    """19:47 and 19:52: the whole bia-draft, the sign-off JSON and the whole pp4-handoff were pasted
    into chat as 'preview' — 'an assistant producing a deliverable, not the BIA agent talking to one
    person. I cannot read that on a phone.' Stage 1 already says the document lives in the file;
    the protocol says it for every stage, and Stage 4 stops asking for full text.

    2026-08-19 (Task 6): the rule stays in the protocol; the worked move that carries it —
    offering 'show the full text' instead of pasting — MOVED into example 2, so it is asserted
    against the whole instruction surface rather than the protocol alone. Case-folded on the
    way: the protocol quoted the phrase mid-sentence, example 2 shows it as move 3 of a real
    card, where it is sentence-cased like every other move."""
    payload = journey_engine.render_stage_tool(_bia(), _bia().first_stage(), 1, 6)
    p = " ".join(journey_engine.STAGE_PROTOCOL.split())
    assert "A preview is a card, not the document" in p
    assert "never paste the whole file" in p
    assert "show the full text" in _instructions(payload).lower()
    s4 = _stage("id: draft-and-review", "id: solution-design")
    assert "full-text previews" not in s4
    assert "card previews" in s4


def test_protocol_writes_for_the_department_head():
    """Hans §5: 'grid-derived MTPD' → 'the 8 h the impact grid gives us' ('I know the word. The
    department head I forward this to does not.'); 'KA-01 target 4 h vs Frostmark P1 recovery 8 h'
    → 'KA-01 — central refrigeration: 4 h target vs supplier 8 h'. Bruno fixed both when asked; the
    protocol makes it the default. ISO/TS 22317 §5.3.1: the reason travels with the number.

    2026-08-19 (Task 6): the rule stays in the protocol, its two worked specimens
    ('KA-01 — central refrigeration', 'reaches the threshold at') MOVED into example 2 — the
    protocol carried them as quoted samples, which is what a worked example is for. Asserted
    against `_instructions`, so a later edit may move them again but never drop them."""
    payload = journey_engine.render_stage_tool(_bia(), _bia().first_stage(), 1, 6)
    p = " ".join(journey_engine.STAGE_PROTOCOL.split())
    assert "plain name" in p and "never a bare id" in p
    instr = _instructions(payload)
    assert "KA-01 — central refrigeration" in instr
    assert "reaches the threshold at" in instr
    s4 = _stage("id: draft-and-review", "id: solution-design")
    assert "grid-derived MTPD" not in s4          # the label the cards parroted
    assert "impact grid gives" in s4


def test_stage1_names_the_categories_it_counts():
    """Hans 5: 'seven impact categories, six horizons, threshold 4' -> 'Seven impact categories -
    I'll name them when we score. Do not count things you will not list.'"""
    s1 = _stage("id: scope-and-risk", "id: capture-transcript")
    assert "name them when we score" in s1


def test_stage4_gate_question_is_one_line():
    """Hans 5: 'May I record the binding worst-case requirement as RTO < 4 h, and flag a recovery
    gap because...' -> 'RTO < 4 h binding, and the 8 h ERP target is a gap. ok?'"""
    s4 = _stage("id: draft-and-review", "id: solution-design")
    assert "the gate question is one line" in s4


def test_protocol_puts_the_risk_result_in_the_file_not_the_turn():
    """Hans 5: 'Risk is medium; use the approved internal environment.' -> belongs in the file.
    2026-08-19: the chat allowance is cut — the judge's item (Hans run (c) §5); the result
    lives in the file only."""
    p = " ".join(journey_engine.STAGE_PROTOCOL.split())
    assert "risk result goes into the saved document" in p
    assert "approved environment, medium risk" not in p


# --- 2026-08-19: "Bruno sounds like a colleague" — Task 0, the falsification anchor -------

def test_stage_payload_budget_2026_08_19():
    """Falsification anchor for the 2026-08-19 voice work: every later change to
    STAGE_PROTOCOL or a stage payload must pay for its own mass instead of drifting for
    free. Measured baseline this session: protocol 2,580 chars; the six run-bia stage
    payloads total 53,513 chars, largest 13,843 — reproduced exactly by
    len(json.dumps(payload, ensure_ascii=False)) with the default separators (ensure_ascii=True
    gives 54,683/14,098; compact separators give 53,070/13,754 — neither matches, so
    ensure_ascii=False with default separators is the size definition this test uses).
    Thresholds sit above the measured baseline (headroom, not a pin) and this test passes
    today by construction — it exists so a later stage grows deliberately, not by accident."""
    assert len(journey_engine.STAGE_PROTOCOL) <= 2580, (
        "STAGE_PROTOCOL grew past its pin — levers: drop `connector_guidance` from the "
        "stage-tool payload (-1,719 chars) or `approval_gate` (-1,203) — pay for growth in "
        "the same commit; never relax this threshold to make it pass")
    bia = _bia()
    total = len(bia.stages)
    sizes = [
        len(json.dumps(journey_engine.render_stage_tool(bia, s, n, total), ensure_ascii=False))
        for n, s in enumerate(bia.stages, start=1)
    ]
    assert max(sizes) <= 14_500, (
        "a stage payload grew past its pin — levers: drop `connector_guidance` from the "
        "stage-tool payload (-1,719 chars) or `approval_gate` (-1,203) — pay for growth in "
        "the same commit; never relax this threshold to make it pass")
    assert sum(sizes) <= 61_000, (
        "the total payload grew past its pin — levers: drop `connector_guidance` from the "
        "stage-tool payload (-1,719 chars) or `approval_gate` (-1,203) — pay for growth in "
        "the same commit; never relax this threshold to make it pass")


def test_budget_anchor_asserts_name_their_levers():
    """Review finding: a bare `assert max(sizes) <= 14_500` gives no guidance when it goes red
    — and headroom is thin (99 chars on draft-and-review as measured this session). A sentence
    added to a stage's yaml in the independently-edited public design/ repo is all it takes.
    Every threshold assert in the budget anchor must name the two levers the plan already
    measured — dropping `connector_guidance` (-1,719 chars) or `approval_gate` (-1,203) from the
    stage-tool payload — and say to pay for growth in the same commit rather than raise the
    pin. Read via ast rather than hardcoding the assert count, so this test breaks (loudly) if
    the anchor's shape changes rather than silently stops checking anything."""
    src = inspect.getsource(test_stage_payload_budget_2026_08_19)
    tree = ast.parse(src)
    asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    assert len(asserts) == 3, "expected exactly the three threshold asserts"
    for a in asserts:
        assert a.msg is not None, f"assert at budget-anchor line {a.lineno} has no message"
    messages = " ".join(ast.get_source_segment(src, a.msg) for a in asserts)
    assert "connector_guidance" in messages
    assert "approval_gate" in messages
    assert "1,719" in messages
    assert "1,203" in messages
    assert "same commit" in messages
    assert "never relax" in messages


# --- 2026-08-19: "Bruno sounds like a colleague" — Task 5, five links become one ---------

def test_verify_for_yourself_blocks_collapse_to_one_source_link():
    """Task 5: the ceremony sentence — 'Close your answer with a "Verify for yourself:" line
    of clickable links:' followed by 3-4 [title](url) links — reads like a compliance form,
    not a colleague. Each of the five stages that carried it (3a never did) collapses to one
    'Source: [title](url).' line; `cites` is untouched, so every chunk id — and the dropped
    links, in the saved documents — is still there. −1,070 chars off the payload budget."""
    assert "Verify for yourself" not in YAML
    assert YAML.count("Source: [") == 5
    per_stage = [
        ("id: scope-and-risk", "id: capture-transcript", "BIA Preparation"),
        ("id: capture-transcript", "id: analyse-transcript", "PP3 minimum controls"),
        ("id: analyse-transcript", "id: asset-owner-capture", "BIA Output Review"),
        ("id: draft-and-review", "id: solution-design", "PP3 outcomes"),
    ]
    for start, end, title in per_stage:
        block = _stage(start, end)
        assert block.count("Source: [") == 1, start
        assert f"Source: [{title}](" in block, (start, title)
    s5 = " ".join(YAML.split("id: solution-design")[1].split())
    assert s5.count("Source: [") == 1
    assert "Source: [AI support for PP4: process](" in s5
    # 3a (the owner side-quest) never carried a Verify block — nothing to collapse there.
    s3a = _stage("id: asset-owner-capture", "id: draft-and-review")
    assert "Source: [" not in s3a


def test_stage_protocol_renders_one_citation_link_per_card():
    """Task 5's other half: STAGE_PROTOCOL told the model to render EVERY citation as a link —
    the five-block ceremony this test's sibling just cut. One link per card, the one that
    matters, matches the new single Source: line above."""
    p = journey_engine.STAGE_PROTOCOL
    assert "Render a citation as one [title](url) link — one per card, the one that matters." in p
    assert "Render each citation" not in p


# --- 2026-08-19: "Bruno sounds like a colleague" — Tasks 2/3/4/6, the voice itself ------

def test_protocol_opens_the_card_only_on_stage_work():
    """Task 2. Run (c): every turn opened with a stage banner — a question about the standards
    basis, a status answer, a save receipt — so Bruno read as a workflow engine narrating
    itself rather than a consultant answering. The card is now conditional on the turn moving
    the stage forward, and the banner-before-the-answer tic is named outright."""
    p = " ".join(journey_engine.STAGE_PROTOCOL.split())
    assert "Begin every turn with a stage card" not in p     # the unconditional form is gone
    assert "The stage card is for stage work" in p
    assert "Every other turn runs bare" in p
    assert "Never announce a turn before taking it" in p
    # the card's text is the server-computed field, not a shape the model assembles
    assert "<name> (<status>)" not in p and "this payload's `card` line" in p


def test_stage_payload_carries_the_persona_voice_and_examples_last():
    """Tasks 3+4: the register (a BCM consultant of twenty years, to a BCM manager reading on a
    phone) and four worked turns ride in the payload the agent reads every turn — appended
    LAST, because models weight the end of a payload. The examples are the demonstration half
    of the protocol: rules say what to do, a worked turn shows it."""
    bia = _bia()
    total = len(bia.stages)
    for n, s in enumerate(bia.stages, start=1):
        payload = journey_engine.render_stage_tool(bia, s, n, total)
        assert list(payload)[-2:] == ["voice", "examples"], s.id
        assert "BCM consultant of twenty years" in payload["voice"], s.id
        assert "Under 700 characters" in payload["voice"], s.id
        # 396 -> 411 on 2026-08-19: "No headings unless there is a list" became "Bold nothing
        # but the stage card line; no headings" — Hans's limit on the newly bolded card, so it
        # stays one header and does not creep back into a generated document.
        assert len(payload["voice"]) == 411, s.id       # the register, not a paraphrase of it
        assert "Bold nothing but the stage card line" in payload["voice"], s.id
        assert len(payload["examples"]) == 4, s.id
        assert all({"when", "bad", "good"} == set(e) for e in payload["examples"]), s.id
    # ...and last in the payload the model actually RECEIVES: both tool entry points append
    # keys of their own after render_stage_tool returns, which would bury the examples.
    start = addendum_tools.start_journey_fn("run-bia")
    assert list(start)[-2:] == ["voice", "examples"]
    assert {"overview", "total_stages"} <= set(start)
    resumed = addendum_tools.next_step_fn("run-bia", "1")
    assert resumed["resumed"] is True
    assert list(resumed)[-2:] == ["voice", "examples"]


def test_worked_examples_obey_the_rule_they_teach():
    """An example that broke its own 700-character rule would be the worst artefact here. The
    `bad` halves stay short too — each is a warning, not a second essay — and each carries one
    distinctive phrase from the real run-(c) transcript so the acceptance script can grep for
    an echo of it in a live turn."""
    examples = journey_engine.render_stage_tool(_bia(), _bia().first_stage(), 1, 6)["examples"]
    for e in examples:
        assert len(e["good"]) <= 700, (e["when"], len(e["good"]))
        assert len(e["bad"]) <= 450, (e["when"], len(e["bad"]))
        assert e["when"] and "\n" not in e["when"], e["when"]
    warnings = " ".join(e["bad"] for e in examples)
    for echo in ("Stage 1 · Identification of scope (standards basis)",
                 'reply exactly: "Approve — owner handover"',
                 "Nothing has been saved.",
                 "byte-identical to the referee-validated record"):
        assert echo in warnings, echo
        # a phrase the acceptance script greps for as a bad-example echo must not ALSO be live
        # guidance — a grep hit would then be ambiguous between "Bruno copied the anti-pattern"
        # and "Bruno followed the protocol".
        assert echo not in journey_engine.STAGE_PROTOCOL, echo
    # 2026-08-19 owner ruling: the label and the numbered list are retired on EVERY turn, stage
    # work included — the closing move is now prose. So no `good` may carry either shape, and
    # every one still ends on something the manager can answer (W11b, never a dead end).
    by_when = {e["when"]: e for e in examples}
    for e in examples:
        assert "Next:" not in e["good"], e["when"]
        assert "\n1. " not in e["good"], e["when"]
        assert e["good"].rstrip()[-1] in "?.", e["when"]
    # The stage-work example must SHOW the inline numbering, or the rule is adjectives again —
    # which is the one encoding that already failed once (owner decision 2, 2026-08-19).
    gate = by_when["the gate card done right"]["good"]
    assert "(1)" in gate and "(2)" in gate and "(3)" in gate
    # The retired shape stays in a `bad` half so the model is shown what it must not copy.
    assert "Next:\n1. " in by_when["a question mid-journey"]["bad"]


def test_example_four_shows_the_receipt_the_server_actually_produces():
    """Task 7 changed the save receipt to say what moved; example 4 must show THAT line, or the
    model is trained on a receipt no lane ever emits.

    Built from `graph_files._size_clause` rather than retyped, so a later change to the receipt
    format fails HERE — a hardcoded literal would stay green while the example went stale, which
    is the whole failure this test exists to catch."""
    examples = journey_engine.render_stage_tool(_bia(), _bia().first_stage(), 1, 6)["examples"]
    receipt = next(e for e in examples if e["when"] == "the save receipt")
    assert receipt["good"].startswith(
        f"✓ Saved: bia-draft.md — {graph_files._size_clause(3955, 4210)}, 6 sections.")
    # A save receipt is a bare turn (Task 2), so it ends on ONE clause the manager can answer in
    # a word — not on 'Next:' and numbered moves, which are for a turn that moves the stage.
    assert "Next:" not in receipt["good"]
    assert receipt["good"].rstrip().endswith("?")
    assert "byte-identical" in receipt["bad"]                # the wording Task 7 retired


@pytest.mark.parametrize("content", [
    None,                                              # file missing entirely
    '{"bia-facilitator": {"voice": "informal"}}',      # object keyed by persona id, not a list
    '["bia-facilitator", "plan-reviewer"]',            # list of bare id strings, not persona dicts
], ids=["missing-file", "object-not-list", "list-of-scalars"])
def test_stage_payload_survives_an_unreadable_personas_file(monkeypatch, tmp_path, content):
    """A broken personas file must not take down a stage. The voice is an enrichment; the
    journey is the contract. `_personas` is memoised, so the cache is reset here too — this
    test would otherwise pass on a warm cache and prove nothing. Three shapes design/
    personas.json can take in an independently-edited public repo: absent; a JSON object keyed
    by persona id instead of a list; and a list of bare id strings instead of persona dicts.
    The latter two raise TypeError out of `{p["id"]: p for p in ...}` (`p` is a str, not a
    dict) — ValueError alone does not catch them."""
    monkeypatch.setattr(journey_engine, "_PERSONAS", None)
    if content is None:
        personas_path = Path("/nonexistent/personas.json")
    else:
        personas_path = tmp_path / "personas.json"
        personas_path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(journey_engine, "PERSONAS_FILE", personas_path)
    payload = journey_engine.render_stage_tool(_bia(), _bia().first_stage(), 1, 6)
    assert payload["name"] == "Stage 1 · Identification of scope"
    assert "voice" not in payload and "examples" not in payload


# --- 2026-08-19: the conduct text moves to the public method package -------------------------

def test_conduct_text_lives_in_the_design_package():
    """Owner ruling 2026-08-19: the public method package carries the method, so a voice edit
    is a design commit rather than a Python deploy. Three revisions of one sentence shipped in
    one afternoon through this file; each cost a full deploy."""
    conduct = (Path(journey_engine.__file__).parent / "design" / "conduct.md").read_text(encoding="utf-8")
    assert "The stage card is for stage work" in conduct
    assert "The stage card is for stage work" not in Path(journey_engine.__file__).read_text(encoding="utf-8")


def test_the_protocol_is_whatever_the_design_package_says():
    """The plumbing guard: STAGE_PROTOCOL is the rendered conduct.md and nothing else, so a
    stage cannot ship a protocol the method package does not contain.

    This used to also pin the length at 2,203 — the count measured before the text moved out of
    journeys.py — which proved the move lost nothing. That job is done and recorded (verified
    against 7eaa6c5, byte-identical), and a frozen length would now block every deliberate
    edit; the first was Hans's choice rule the same evening. Size is the budget anchor's job."""
    assert journey_engine.load_conduct() == journey_engine.STAGE_PROTOCOL
    assert journey_engine.STAGE_PROTOCOL.startswith("Present ONLY this stage")
    assert journey_engine.STAGE_PROTOCOL.endswith("every number with its reason in words.")


def test_a_broken_conduct_file_degrades_to_no_protocol_rather_than_no_stage():
    """Same discipline as _personas(): the voice is an enrichment, the journey is the contract.
    A stage the user is standing in matters more than the register it is delivered in."""
    assert journey_engine.load_conduct(Path("/nonexistent/conduct.md")) == ""


def test_no_artifact_word_reaches_the_model():
    """W11a — Willem's first blocker. "artifact" is clean on every surface a user reads: not in
    the rendered conduct, not in Part D, not in the persona. The residual was model-visible: the
    payload key `document_contracts`, once per payload in all six, read by the model every stage
    and echoable straight back at a BCM manager who has never heard the word.

    Asserts on the whole serialised payload, keys included — checking the values alone is what
    let a key name survive a vocabulary sweep that was otherwise complete."""
    journey = journey_engine.load_journeys()["run-bia"]
    total = len(journey.stages)
    offenders = []
    for i, stage in enumerate(journey.stages, start=1):
        blob = json.dumps(journey_engine.render_stage_tool(journey, stage, i, total), ensure_ascii=False)
        if "artifact" in blob.lower():
            offenders.append(f"payload:{stage.id}")
    # The MCP tool descriptions are the other model-visible surface, and the harder one: the
    # model reads them EVERY turn, where a stage payload only reaches a turn that calls a tool.
    # The roadmap counted the payload key as the single residual; it is not.
    src = Path("server.py").read_text()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg not in {"description", "title"}:
                continue
            text = " ".join(n.value for n in ast.walk(kw.value)
                            if isinstance(n, ast.Constant) and isinstance(n.value, str))
            if "artifact" in text.lower():
                name = next((k.value.value for k in node.keywords
                             if k.arg == "name" and isinstance(k.value, ast.Constant)), "?")
                offenders.append(f"tool:{name}")
    # The third model-visible surface, and the one that reached a USER's screen: the strings a
    # refusal hands back. On 2026-08-20 a live Teams turn relayed "rejected as a summary rather
    # than the full artifact" verbatim to the owner — the word Willem named as his first blocker,
    # printed by the workflow itself. Comments never reach the model and are not in the AST at
    # all; docstrings are covered by the tool-description sweep above, so only the plain string
    # constants are graded here.
    gf = ast.parse(Path("graph_files.py").read_text())
    docstrings = {d for node in ast.walk(gf)
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module))
                  for d in [ast.get_docstring(node, clean=False)] if d}
    for node in ast.walk(gf):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and "artifact" in node.value.lower() and node.value not in docstrings):
            offenders.append(f"refusal:graph_files.py:{node.lineno}")
    assert not offenders, f"'artifact' still reaches the model in: {sorted(set(offenders))}"


def test_requires_reads_is_declared_and_never_reaches_the_payload():
    """The declaration stays server-side; the PATHS are now named once, in tools_to_use only.

    **This reverses half of the 2026-08-20 read-gate decision, deliberately.** That design said
    enforcement is free because the declaration never ships, and it was right about the budget
    and wrong about who pays: the model was never told which documents to read, so it drafted
    first and met the requirement as a refusal. Three live runs that day; the Logistics one
    turned one missing pair into two refusals, two narrated approval turns and two save
    previews. The cost did not disappear, it moved from the payload to the person.

    The original cost estimate also assumed all six stages. Only stage 1 declares reads, so the
    real price is +111 characters on one stage, and stage 1 is not the largest — payload_max is
    untouched.

    What is still pinned, and why each half matters: the KEY never ships (it is machinery, and
    reviewer_checklist and expected_output set that precedent), and the paths appear ONLY inside
    tools_to_use — not duplicated into the prompt, the goal or the gate, which is how a
    server-side declaration quietly becomes six payloads of prose."""
    journey = journey_engine.load_journeys()["run-bia"]
    stage = journey.stage("scope-and-risk")
    assert stage.requires_reads, "stage 1 must declare the documents it proposes from"
    payload = journey_engine.render_stage_tool(journey, stage, 1, len(journey.stages))
    assert "requires_reads" not in json.dumps(payload, ensure_ascii=False)
    elsewhere = json.dumps({k: v for k, v in payload.items() if k != "tools_to_use"},
                           ensure_ascii=False)
    for path in stage.requires_reads:
        assert path not in elsewhere, f"{path} leaked outside tools_to_use"


def test_every_stage_offers_a_numbered_move():
    """H4(a)/W11(b) — Willem could not get started because he had to type "continue". There
    is no such magic word in the method; the cause was turns ending with nothing to answer.
    Every stage must therefore carry at least one move the agent can end its turn on. The
    other half of this — a bare turn that calls no tool — lives in Part D and reaches Teams
    only when the owner pastes it."""
    journey = journey_engine.load_journeys()["run-bia"]
    bare = [s.id for s in journey.stages if not s.next_moves]
    assert not bare, f"stages with nothing to offer the user: {bare}"


def test_stage_four_claims_no_machine_check_it_does_not_have():
    """The record carries no seats and no headcount field, so nothing can compare them —
    yet stage 4 told the user "Skeleton staff or seats above the department headcount are
    flagged too", in the same breath as two checks the referee really does enforce. Owner
    ruling 2026-08-20: delete the promise rather than half-build the schema. Stage 5 still
    asks the agent to eyeball seats against headcount by judgment; that claims nothing
    about the machine and stays."""
    journey = journey_engine.load_journeys()["run-bia"]
    stage = journey.stage("draft-and-review")
    blob = json.dumps(
        journey_engine.render_stage_tool(journey, stage, 4, len(journey.stages)),
        ensure_ascii=False)
    assert "flagged too" not in blob
    assert "headcount" not in blob, "stage 4 must not claim a seats/headcount check"


def test_the_protocol_never_asks_permission_for_its_own_next_step():
    """Testers, 2026-08-20: six presses of `1` to reach Stage 2, and only three of those turns
    carried a decision. The rest asked permission to keep working — "1 - yes — generate the
    final save preview" — which is the `continue` magic word W11(b) deleted, wearing a number.

    The gates were not the cost. That run's call log is one write refusal (10:10:29Z), two
    reads, one clean save (10:11:47Z) and next_step ok (10:12:05Z): zero rework. The clicks
    came from one clause — "what you would do by default" — which turns the agent's own next
    action into something to ask about, on every stage turn, in all six payloads."""
    p = " ".join(journey_engine.STAGE_PROTOCOL.split())
    assert "what you would do by default" not in p, "the clause that manufactures confirmations"
    assert "End every turn" not in p, "a turn with no decision in it must not invent one"
    assert "already approved" in p, "say what to do instead, do not merely delete the old rule"
    assert "refusal" in p, "a server refusal is not a decision to put to the user"
    # Hans's ruling on the rendering survives untouched — this changes WHEN a menu appears.
    assert "1 yes — use this scope" in p and "equals sign" in p


def test_a_stage_declares_the_reads_it_will_be_refused_for():
    """The whole confirmation loop starts here. Stage 1 told the model to call
    `identify_ai_risks` and `get_prompt_template` — neither is a read — while `requires_reads`
    stayed server-side to save payload. So the model did exactly what it was told, drafted, and
    discovered the requirement only by being refused: live 2026-08-20, three separate runs each
    hit the gate AFTER drafting, and the Logistics run narrated two refusals as two approval
    turns and two save previews.

    Fixing the refusal is fixing the symptom. `tools_to_use` is the field whose entire job is
    'which tools to call for this stage', and it is derived here from `requires_reads` so it
    cannot drift from what the server will actually enforce."""
    bia = _bia()
    total = len(bia.stages)
    for n, s in enumerate(bia.stages, start=1):
        payload = journey_engine.render_stage_tool(bia, s, n, total)
        for path in s.requires_reads:
            assert any(path in str(t) for t in payload["tools_to_use"]), (
                f"stage {s.id} will be refused for {path} but never names it in tools_to_use")
        # the reads come first: a tool list is read in order, and drafting is what must wait
        if s.requires_reads:
            assert str(payload["tools_to_use"][0]).startswith("read_company_file"), s.id
