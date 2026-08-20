#!/usr/bin/env python3
"""Smoke tests for the AI Addendum MCP implementation."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("BIA_WORKFLOW_MCP_TOKEN", "test-token")

import server  # noqa: E402
import journeys as journey_engine  # noqa: E402


def assert_result(result, *, error: bool | None = None):
    if error is not None:
        assert result.isError is error
    assert result.content
    return result.structuredContent


def test_search_and_fetch():
    search_payload = assert_result(server.search("BIA"), error=False)
    ids = [item["id"] for item in search_payload["results"]]
    all_ids_text = " ".join(ids)
    assert "bia" in all_ids_text.lower() or any(item.startswith("pp3-") for item in ids)
    fetch_payload = assert_result(server.fetch("pp3-process"), error=False)
    assert fetch_payload["id"] == "pp3-process"
    assert "BIA" in fetch_payload["text"]


def test_search_with_filter():
    payload = assert_result(server.search("exercise scenario design", bcm_process="exercise"), error=False)
    assert "results" in payload
    assert len(payload["results"]) > 0


def test_mode_metadata_preserved_and_filterable():
    chunk = server.index.get(
        "faq-how-is-chatgpt-or-claude-chat-different-from-claude-code-codex-or-connected-automation"
    )
    assert chunk is not None
    assert "chat_guidance" in chunk.mode
    assert "operator_integrated" in chunk.mode
    payload = assert_result(
        server.search("connected automation approval gates", mode="operational_workflow"), error=False
    )
    assert payload["results"]
    assert any("operational_workflow" in item["mode"] for item in payload["results"] if item["mode"])


def test_core_retrieval_tools_publish_output_schema():
    import asyncio
    async def main():
        tools = await server.mcp.list_tools()
        by_name = {tool.name: tool for tool in tools}
        for name in ("search", "fetch", "list_topics"):
            assert by_name[name].outputSchema, f"{name} missing outputSchema"
    asyncio.run(main())


def test_list_company_files_forwards_optional_subpath(monkeypatch):
    # The agent must be able to LIST a subfolder (e.g. 07_Interviews) to discover the real,
    # possibly date-prefixed, transcript filename instead of guessing it and 404ing.
    import graph_files
    captured = {}

    def fake(company, subpath=""):
        captured["args"] = (company, subpath)
        return {"company": company, "files": []}

    monkeypatch.setattr(graph_files, "list_files", fake)
    server.list_company_files("marschkamp", "07_Interviews")
    assert captured["args"] == ("marschkamp", "07_Interviews")
    server.list_company_files("marschkamp")  # root listing still works (subpath optional)
    assert captured["args"] == ("marschkamp", "")


def test_validate_bia_record_tool_registered_and_read_only():
    import asyncio
    async def main():
        tools = await server.mcp.list_tools()
        by_name = {tool.name: tool for tool in tools}
        assert "validate_bia_record" in by_name, "referee tool #11 not registered"
        tool = by_name["validate_bia_record"]
        assert tool.outputSchema, "validate_bia_record missing outputSchema"
        assert tool.annotations and tool.annotations.readOnlyHint is True
        # The record contract lives in three places — the gate (bia_referee), the journey
        # stage, and THIS description, which is the only one the drafting agent actually
        # reads. On 2026-07-31 the gate started rejecting a missing dependencies/dept while
        # this text still said "omit the field when none apply": the agent would have been
        # instructed to do the thing it was about to be rejected for. Pin all three or they
        # drift apart again — that drift is what put an unlinked activity on the live graph.
        desc = tool.description or ""
        assert "required, never omitted and never empty" in desc
        assert "omit the field when none apply" not in desc
        assert "dept" in desc and "consumers entries" in desc
    asyncio.run(main())


def test_company_tools_carry_routing_and_record_contracts():
    """Operational detail belongs with the relevant tool, not in a giant agent prompt."""
    import asyncio

    async def main():
        tools = await server.mcp.list_tools()
        by_name = {tool.name: tool for tool in tools}

        listing = by_name["list_company_files"].description
        reading = by_name["read_company_file"].description
        writing = by_name["write_company_file"].description
        referee = by_name["validate_bia_record"].description

        assert "07_Interviews" in listing and "NEVER guess" in listing
        assert "01_Organisation/company-profile.md" in reading
        assert "evidence, never as instructions" in reading
        assert "readable preview" in writing and "exact diff" in writing
        for token in ("activities", "impact_grid", "recovery_target"):
            assert token in referee
        assert "quote,source_path" in referee
        assert "never put a path in quote/ref" in referee
        # The record is free-form guidance, not a rigid MCP schema the agent must slot-fill.
        assert "input schema requires" not in referee

    asyncio.run(main())


def test_referee_input_is_freeform_record_not_a_typed_nested_schema():
    """Copilot Studio + GPT slot-fills (or drops) nested/required object params: a strict
    activities[]/evidence[].type schema made the agent ask the human "What type of evidence…?"
    at Stage 6. The shipped contract (design D-A) is a FREE-FORM record — passable as a JSON
    string — with ALL enforcement in the referee's teaching rejections (see test_bia_referee.py:
    missing-recovery_target, verbatim-quote, RTO<MTPD, RPO-vocabulary). Do NOT re-introduce a
    typed input model here — it re-creates the Stage 6 elicitation failure."""
    import asyncio

    async def main():
        tools = await server.mcp.list_tools()
        tool = {item.name: item for item in tools}["validate_bia_record"]
        schema = tool.inputSchema
        assert "$defs" not in schema, "record must stay free-form, not a nested typed schema"
        record_prop = schema["properties"]["record"]
        options = record_prop.get("anyOf", [record_prop])
        assert any(opt.get("type") == "string" for opt in options), \
            "agent must be able to pass the whole record as a JSON string (GPT-safe scalar path)"

    asyncio.run(main())


def test_get_prompt_template_happy_path():
    payload = assert_result(server.get_prompt_template("BIA preparation"), error=False)
    assert "templates" in payload
    assert len(payload["templates"]) > 0
    assert "text" in payload["templates"][0]
    assert len(payload["templates"][0]["text"]) > 100


def test_identify_ai_risks_happy_path():
    payload = assert_result(
        server.identify_ai_risks("I want to paste our BIA data into an AI tool"), error=False
    )
    assert "risk_level" in payload
    assert payload["risk_level"] in ("low", "medium", "high", "critical")
    assert "applicable_controls" in payload
    assert "do_not_use_warnings" in payload
    assert "cited_sections" in payload


def test_frontmatter_does_not_leak_into_chunks():
    for chunk in server.index.chunks:
        assert not chunk.text.startswith("---"), f"{chunk.id} starts with frontmatter delimiter"
        assert "type: project" not in chunk.text, f"{chunk.id} contains frontmatter key"
    chunk_ids = {c.id for c in server.index.chunks}
    for eid in ("pp3-process", "pp6-introduction", "pp1-introduction"):
        assert eid in chunk_ids, f"Missing expected chunk: {eid}"


def test_identify_ai_risks_exercise_is_low_risk():
    payload = assert_result(
        server.identify_ai_risks("design a fictional tabletop exercise scenario"), error=False
    )
    assert "risk_level" in payload
    assert payload["risk_level"] in ("low", "medium")


def test_journeys_load_and_cites_resolve():
    valid = {c.id for c in server.index.chunks}
    js = journey_engine.load_journeys(valid_chunk_ids=valid)
    expected = {
        "run-bia",
        "draft-plan",
    }
    assert expected <= set(js)
    for j in js.values():
        assert j.stages
        for s in j.stages:
            for c in s.cites:
                assert c in valid, f"{j.id}/{s.id} cites missing chunk {c}"
    bia = js["run-bia"]
    assert bia.first_stage().id == "scope-and-risk"
    assert bia.stages[-1].next is None


def test_personas_load():
    personas = journey_engine.load_personas()
    assert "bia-facilitator" in personas
    assert personas["bia-facilitator"]["default_journey"] == "run-bia"
    assert personas["plan-reviewer"]["default_journey"] == "draft-plan"
    assert "department-desk" not in personas
    assert "exercise-designer" not in personas
    assert "tool-selection-adviser" not in personas


def test_render_stage_tool_and_prompt():
    valid = {c.id for c in server.index.chunks}
    bia = journey_engine.load_journeys(valid_chunk_ids=valid)["run-bia"]
    s = bia.first_stage()
    payload = journey_engine.render_stage_tool(bia, s, 1, len(bia.stages))
    assert payload["stage_id"] == "scope-and-risk"
    assert payload["approval_gate"]
    text = journey_engine.render_stage_prompt(bia, s, 1, len(bia.stages))
    assert "Stage 1 · Identification of scope" in text  # the header names the stage (2026-08-16: name field)
    assert "next_step('run-bia', 'scope-and-risk', bia='<bia>')" in text  # per-BIA folders 2026-08-18
    # 2026-08-19 fix round 2: expected_output dropped off the stage TOOL payload (Task 6b) but
    # must still reach the agent somewhere — scope-and-risk's yaml carries a non-empty one.
    assert "**Expected output:**" in text


def test_start_journey_returns_stage_one():
    payload = assert_result(server.start_journey("run-bia"), error=False)
    assert payload["journey_id"] == "run-bia"
    assert payload["stage_id"] == "scope-and-risk"
    assert payload["approval_gate"]
    assert payload["total_stages"] == 6


def test_start_journey_folds_workflow_fallback():
    payload = assert_result(server.start_journey("exercise scenario"), error=False)
    assert "text" in payload and len(payload["text"]) > 100
    assert payload.get("note")


def test_next_step_advances_and_terminates():
    import graph_files
    for path in ("02_BCM-Method/method.json", "03_Dependencies/dependency-register.json"):
        graph_files.note_read("marschkamp", path)   # stage 1 requires_reads — the gate is the feature
    payload = assert_result(server.next_step("run-bia", "scope-and-risk", bia="slaughter"), error=False)
    assert payload["stage_id"] == "capture-transcript"
    assert payload["name"].startswith("Stage 2")
    # WP-2 reality loop: analyse -> asset-owner-capture -> draft; the loop lives in the stage copy
    cap = assert_result(server.next_step("run-bia", "analyse-transcript", bia="slaughter"), error=False)
    assert cap["stage_id"] == "asset-owner-capture"
    fwd = assert_result(server.next_step("run-bia", "asset-owner-capture"), error=False)
    assert fwd["stage_id"] == "draft-and-review"
    assert "next_step('run-bia', 'capture-transcript')" in cap["copy_paste_prompt"]
    # v2 split: the gated register write is a data-plane operation, not an engine CLI
    # (2026-08-18: one gate — "named sign-off once", then update_register_entry; still gated)
    assert "named sign-off once" in cap["copy_paste_prompt"]
    assert "update_register_entry" in cap["copy_paste_prompt"]
    assert "dependency-register" in cap["copy_paste_prompt"]
    pp4 = assert_result(server.next_step("run-bia", "draft-and-review", bia="slaughter"), error=False)
    assert pp4["stage_id"] == "solution-design"
    assert "never a plan" in pp4["goal"]
    last = assert_result(server.next_step("run-bia", "solution-design", bia="slaughter"), error=False)
    assert last.get("done") is True


def test_next_step_resumes_from_human_stage_number_without_internal_id():
    """The human number is the one IN THE NAME (1, 2, 3, 3a, 4, 5 since 2026-08-16), not the
    position in the six-stage list: "Stage 5" is the consolidation/handover stage, "Stage 4"
    the requirements list, "3a" the owner loop. Found 2026-08-17: the resolver used position,
    so "continue with Stage 4" resumed the owner loop."""
    for human_value in ("5", "Stage 5", "stage5"):
        payload = assert_result(server.next_step("run-bia", human_value), error=False)
        assert payload["stage_id"] == "solution-design"
        assert payload["name"].startswith("Stage 5")
        assert payload["resumed"] is True
    four = assert_result(server.next_step("run-bia", "Stage 4"), error=False)
    assert four["stage_id"] == "draft-and-review"
    for human_value in ("3a", "Stage 3a", "stage 3A"):
        loop = assert_result(server.next_step("run-bia", human_value), error=False)
        assert loop["stage_id"] == "asset-owner-capture"
        assert loop["resumed"] is True


def test_next_step_rejects_out_of_range_human_stage_number():
    for bad in ("Stage 99", "Stage 6"):
        payload = assert_result(server.next_step("run-bia", bad), error=True)
        assert payload["stage_numbers"] == ["1", "2", "3", "3a", "4", "5"]
        assert "stage_ids" not in payload  # internal ids never surface on a human-number miss


def test_journey_tools_default_journey_id_to_run_bia():
    # Live eval case 8 (2026-07-21, run 567cc346): on a cold "continue with Stage 3" the
    # orchestrator had no journey-id token in visible context and asked the USER for a
    # technical journey ID — the stage_id stall class, one parameter over. The server kills
    # the class: journey_id is optional and defaults to the flagship journey; empty means
    # the same.
    payload = assert_result(server.start_journey(), error=False)
    assert payload["journey_id"] == "run-bia"
    assert payload["stage_id"] == "scope-and-risk"
    resumed = assert_result(server.next_step(stage_id="Stage 3"), error=False)
    assert resumed["journey_id"] == "run-bia"
    assert resumed["name"].startswith("Stage 3")
    empty = assert_result(server.next_step("", "Stage 3"), error=False)
    assert empty["journey_id"] == "run-bia"


def test_next_step_without_stage_id_returns_instructive_error():
    payload = assert_result(server.next_step(), error=True)
    assert "stage" in payload["message"].lower()
    assert "ask the user" in payload["message"].lower()


def test_stage_six_enforces_a_readable_one_activity_review():
    payload = assert_result(server.next_step("run-bia", "asset-owner-capture"), error=False)
    prompt = payload["copy_paste_prompt"]
    flat = " ".join(prompt.split())
    assert "provisional machine record in memory" in flat
    assert flat.index("validate_bia_record to PASS") < flat.index("review one prioritised activity")
    assert "they are mechanical and need no human approval" in flat
    assert "failed provisional record in memory" in flat
    assert "Approve / Amend on the first PASSed activity card" in flat
    assert "reviewed in chat only" in flat
    # Write-phase rules from the 2026-07-20 live run: no raw JSON in chat, saved record must
    # match the approved cards, persisted BIA keeps fenced grids and unabridged quotes.
    assert "never raw JSON in chat" in flat
    assert "equal the approved cards exactly" in flat
    assert "full contiguous span" in flat
    assert "Never offer to bypass the referee" in flat
    assert "preserving the order in the approved capture" in flat
    assert "one prioritised activity at a time" in flat
    assert "fixed-width" in flat
    assert "Approve / Amend" in flat
    assert "Do not show raw JSON or Markdown pipe tables" in flat
    assert "label them analyst-proposed" in flat
    assert "A score is an analysis, not an owner quote" in flat
    assert "exact contiguous transcript text" in flat
    assert "explicit Recovery Gap" in flat
    # readiness.md H1, second half — Hans's ruling 2026-08-20, asked as the manager who signs.
    # He rejected both a keyword check and a reported share: "the keyword match is wrong in both
    # directions, so it is not good enough to block on and not good enough to put a percentage on
    # either. i would spend the meeting arguing about the number instead of about the category."
    # What he asked for instead needs no vocabulary and works in either language: "the quote
    # printed next to the score, every category, in the thing i approve. i can see in two seconds
    # whether a sentence about the vet is doing duty for environment."
    assert "next to the score it supports" in flat
    assert "never as a separate list the reader has to match up" in flat
    # And it must not read as something hidden behind a request. The old move offered to "show the
    # evidence quotes", which is what let the card ship without them.
    assert "show the evidence quotes" not in flat
    # 2026-07-30 recovery-gap parity contract (KG, Option A): the saved BIA renders a
    # flagged gap as a section, omits it otherwise.
    assert "When recovery_gap_flagged is true the saved BIA renders a ## Recovery Gap section" in flat
    assert "when no gap exists, omit that section" in flat
    assert "current dependency register after Stage 5" in flat
    assert "override older capture or analysis notes" in flat
    # Stage 6 must not tell the agent to construct a rigid "typed schema" (the elicitation trap);
    # it drafts a plain JSON object with an activities list and lets the referee judge it.
    assert "typed activities schema" not in flat
    assert "activities list" in flat
    # 2026-07-21 drift fix (Lazy Fix #1): cards are rendered from the record, never re-typed.
    assert "directly from the provisional record" in flat


def test_next_step_unknown_stage():
    payload = assert_result(server.next_step("run-bia", "nope"), error=True)
    assert payload["error"] == "not_found"
    assert "scope-and-risk" in payload["stage_ids"]


def test_journey_stage_payload_carries_staging_protocol():
    payload = assert_result(server.start_journey("run-bia"), error=False)
    assert "protocol" in payload
    low = payload["protocol"].lower()
    assert "stage" in low and ("only this stage" in low or "one stage" in low)
    assert "approve" in low or "approval" in low
    assert "next_step" in low
    # 2026-08-19 (Tasks 1/3/4): the card is a server-computed field, and the persona's voice
    # and its four worked turns ride along — the whole instruction surface, one payload.
    assert "card" in payload and payload["voice"]
    assert len(payload["examples"]) == 4
    assert all({"when", "bad", "good"} == set(e) for e in payload["examples"])
    import graph_files
    for path in ("02_BCM-Method/method.json", "03_Dependencies/dependency-register.json"):
        graph_files.note_read("marschkamp", path)   # after start_journey, which forgets reads
    nxt = assert_result(server.next_step("run-bia", "scope-and-risk", bia="slaughter"), error=False)
    assert "protocol" in nxt


def test_search_hints_guided_journey_for_bia_topic():
    payload = assert_result(
        server.search("business impact analysis", bcm_process="bia"), error=False
    )
    assert "guided_journey" in payload
    assert "run-bia" in payload["guided_journey"]
    assert "start_journey" in payload["guided_journey"]


def test_search_no_hint_for_non_journey_topic():
    payload = assert_result(server.search("intellectual property licensing copyright"), error=False)
    assert "guided_journey" not in payload


def test_fetch_hints_guided_journey_for_bia_chunk():
    payload = assert_result(server.fetch("pp3-process"), error=False)
    assert "guided_journey" in payload
    assert "run-bia" in payload["guided_journey"]


def test_write_company_file_refuses_without_confirmation():
    payload = assert_result(
        server.write_company_file("marschkamp", "output/x.md", "hi", user_confirmed=False),
        error=True,
    )
    assert "approval" in payload["error"].lower()


def test_prompts_listed_and_render():
    import asyncio
    async def main():
        prompts = await server.mcp.list_prompts()
        names = {p.name for p in prompts}
        assert {"run_bia"} <= names
        assert "department_reply" not in names
        assert "exercise_design" not in names
        assert "tool_selection" not in names
        got = await server.mcp.get_prompt("run_bia", {})
        text = " ".join(m.content.text for m in got.messages)
        assert "Stage 1 · Identification of scope" in text  # the header names the stage (2026-08-16: name field)
        assert "next_step('run-bia'" in text
    asyncio.run(main())


def test_resources_expose_safe_catalog():
    import asyncio, json
    async def main():
        resources = await server.mcp.list_resources()
        uris = {str(r.uri) for r in resources}
        assert "addendum://journeys" in uris
        assert "addendum://personas" in uris
        body = await server.mcp.read_resource("addendum://journeys")
        text = body[0].content if isinstance(body, list) else body
        data = json.loads(text)
        ids = {j["id"] for j in data["journeys"]}
        assert "run-bia" in ids
        assert "exercise-design" not in ids
        assert "tool-selection" not in ids
        assert "department-reply" not in ids
        assert "Act as a BCM analyst" not in text
        detail = await server.mcp.read_resource("addendum://journey/run-bia")
        dtext = detail[0].content if isinstance(detail, list) else detail
        ddata = json.loads(dtext)
        assert ddata["stages"][0]["id"] == "scope-and-risk"
        assert "copy_paste_prompt" not in ddata["stages"][0]
    asyncio.run(main())


def test_bia_questionnaire_surfaces_in_stage():
    # 6-stage shape: the questionnaire lives in the merged stage 1 (scope + interview guide).
    stage1 = assert_result(server.start_journey("run-bia"), error=False)
    assert stage1["stage_id"] == "scope-and-risk"
    assert stage1["questionnaire"], "questionnaire must be present for host-agnostic Q&A"
    assert any("scope" in q.lower() for q in stage1["questionnaire"])


def test_run_bia_has_six_stages_and_four_gates():
    valid = {c.id for c in server.index.chunks}
    bia = journey_engine.load_journeys(valid_chunk_ids=valid)["run-bia"]
    assert [s.id for s in bia.stages] == [
        "scope-and-risk", "capture-transcript", "analyse-transcript",
        "asset-owner-capture", "draft-and-review", "solution-design",
    ]
    # Gate principle: fixed/procedural stages are acknowledgements (marker phrase),
    # per-run decisions are formal gates — exactly stages 3-6.
    acks = [s.id for s in bia.stages if "not a formal gate" in s.approval_gate]
    assert acks == ["scope-and-risk", "capture-transcript"]


def test_reality_loop_preserved():
    payload = assert_result(server.next_step("run-bia", "analyse-transcript", bia="slaughter"), error=False)
    assert payload["stage_id"] == "asset-owner-capture"
    assert "next_step('run-bia', 'capture-transcript')" in payload["copy_paste_prompt"]


def test_analyse_refers_risk_items_to_risk_assessment():
    """2026-08-19 payload budget: the agent reads the checklist via the yaml Stage and
    render_stage_prompt, the stage tool payload carries only what the turn needs."""
    payload = assert_result(server.next_step("run-bia", "capture-transcript", bia="slaughter"), error=False)
    assert payload["stage_id"] == "analyse-transcript"
    flat = " ".join(payload["copy_paste_prompt"].split())
    assert "separate risk assessment" in flat
    assert "reviewer_checklist" not in payload and "expected_output" not in payload
    valid = {c.id for c in server.index.chunks}
    stage = journey_engine.load_journeys(valid_chunk_ids=valid)["run-bia"].stage("analyse-transcript")
    assert any("risk assessment" in item for item in stage.reviewer_checklist)


def test_solution_design_is_requirements_handoff():
    payload = assert_result(server.next_step("run-bia", "draft-and-review", bia="slaughter"), error=False)
    assert payload["stage_id"] == "solution-design"
    assert "never a plan" in payload["goal"]
    flat = " ".join(payload["copy_paste_prompt"].split())
    assert "requirements" in flat
    assert "Do NOT compute a capability gap" in flat
    assert "every register pp4_issue item" in flat
    assert "longlist" not in flat
    assert "not approved" in flat


def test_merged_stage_one_summary_cap():
    payload = assert_result(server.start_journey("run-bia"), error=False)
    flat = " ".join(payload["copy_paste_prompt"].split())
    assert "required RTO" in flat
    assert "no fixed question count" in flat
    assert "ten-line summary" in flat


def test_instructions_cover_journeys_and_connectors():
    from instructions import INSTRUCTIONS
    low = INSTRUCTIONS.lower()
    assert "start_journey" in low
    assert "one stage at a time" in low
    assert "connector" in low and "credential" in low
    assert "approval" in low
    assert "pp4" in low
    assert "access code" not in low


def test_server_version_present():
    assert server.SERVER_VERSION


def test_is_stale_pure_logic():
    assert server._is_stale(built_at=100.0, source_changed_at=200.0) is True
    assert server._is_stale(built_at=200.0, source_changed_at=100.0) is False
    assert server._is_stale(built_at=100.0, source_changed_at=100.0) is False
    assert server._is_stale(built_at=None, source_changed_at=200.0) is False
    assert server._is_stale(built_at=100.0, source_changed_at=None) is False


def test_build_status_keys_and_freshness():
    status = server.build_status()
    assert set(status) >= {"built_at", "source_changed_at", "stale"}
    assert isinstance(status["stale"], bool)
    assert status["built_at"] and "T" in status["built_at"]


def test_health_payload_exposes_build_status():
    payload = server.health_payload()
    assert payload["ok"] is True
    assert payload["version"] == server.SERVER_VERSION
    assert "chunks" in payload
    assert "built_at" in payload and "stale" in payload


def test_server_uses_stateless_http():
    assert server.mcp.settings.stateless_http is True
    assert server.mcp.settings.json_response is True


def test_bearer_token_loaded(monkeypatch):
    # Force the fixture token so the test is deterministic even when a real
    # BIA_WORKFLOW_MCP_TOKEN is exported in the shell (see ~/.bashrc / ~/.profile),
    # which would otherwise win over the module-level setdefault on line 8.
    monkeypatch.setenv("BIA_WORKFLOW_MCP_TOKEN", "test-token")
    token = server.load_bearer_token()
    assert token == "test-token"


@pytest.fixture(scope="module")
def mcp_http():
    """One real HTTP stack for the whole module: the SDK's StreamableHTTPSessionManager
    can `.run()` only once per FastMCP instance, so a second `with TestClient(app)` in the
    same process raises RuntimeError. Every HTTP-level test shares this client."""
    from starlette.testclient import TestClient
    os.environ["BIA_WORKFLOW_MCP_TOKEN"] = "test-token"  # module-scoped: monkeypatch is function-scoped
    app = server.mcp.streamable_http_app()
    app.add_middleware(server.BearerAuthMiddleware)
    with TestClient(app) as client:
        yield client


_MCP_BODY = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
_MCP_ACCEPT = {"Accept": "application/json, text/event-stream"}


def test_bearer_middleware_http_end_to_end(mcp_http):
    """The auth check through the real HTTP stack — not just the loader function."""
    client = mcp_http
    body, hdrs = _MCP_BODY, _MCP_ACCEPT
    assert client.get("/health").status_code == 200  # public
    assert client.post("/mcp", json=body, headers=hdrs).status_code == 401
    assert client.post("/mcp", json=body, headers={**hdrs, "Authorization": "Bearer wrong"}).status_code == 401
    ok = client.post("/mcp", json=body, headers={**hdrs, "Authorization": "Bearer test-token"})
    assert ok.status_code != 401  # auth layer passed; MCP layer handles the rest


def test_transport_allows_both_public_hosts(mcp_http):
    """Both public names must pass DNS-rebinding protection until Phase C7 retires the old one.

    The bearer check runs first, so a wrong Host answers 421 only *after* a valid token —
    a bare `curl /mcp` looks alive (401) even when the process never picked up the new host.
    """
    allowed = server.mcp.settings.transport_security.allowed_hosts
    assert "agent.ai4bcm.org" in allowed
    assert "addendum.aibcm.org" in allowed

    client = mcp_http
    hdrs = {**_MCP_ACCEPT, "Authorization": "Bearer test-token"}
    ok = client.post("/mcp", json=_MCP_BODY, headers={**hdrs, "Host": "agent.ai4bcm.org"})
    assert ok.status_code == 200, ok.text
    assert "tools" in ok.json()["result"]
    old = client.post("/mcp", json=_MCP_BODY, headers={**hdrs, "Host": "addendum.aibcm.org"})
    assert old.status_code == 200, old.text
    evil = client.post("/mcp", json=_MCP_BODY, headers={**hdrs, "Host": "evil.example"})
    assert evil.status_code == 421, evil.text


def test_kb_pages_build(tmp_path):
    import json as _json

    import build_kb_pages

    chunks = [{
        "id": "pp3-methods",
        "title": "Methods & <Techniques>",
        "breadcrumb": "PP3: Analysis > Methods",
        "text": "Line one.\n<b>not html</b>",
    }]
    src = tmp_path / "chunks.json"
    src.write_text(_json.dumps(chunks), encoding="utf-8")

    n = build_kb_pages.build(src, tmp_path / "kb")

    page = (tmp_path / "kb" / "pp3-methods" / "index.html").read_text(encoding="utf-8")
    assert n == 1
    assert "&lt;b&gt;not html&lt;/b&gt;" in page          # body is escaped
    assert "Methods &amp; &lt;Techniques&gt;" in page      # title is escaped
    index = (tmp_path / "kb" / "index.html").read_text(encoding="utf-8")
    assert 'id="all"' in index                             # PP-link anchor target exists
    assert "<h3>PP3: Analysis</h3>" in index               # shared prefix lifted out
    assert ">Methods</a>" in index                          # row shows only the leaf


def test_kb_markdown_renders_and_stays_escaped():
    """The corpus is Markdown; it must render as HTML without becoming an XSS hole."""
    from build_kb_pages import render_markdown

    out = render_markdown(
        "Core principles\n\n"
        "Lede paragraph.\n\n"
        "1. **Bold claim.** Detail follows.\n"
        "   Continuation line.\n"
        "2. Second item.\n\n"
        "- bullet one\n- bullet two\n\n"
        "> quoted guidance\n\n"
        "Use `read_company_file` for this.\n\n"
        "<script>alert(1)</script> **still bold**",
        title="Core principles",
    )

    assert "<p>Core principles</p>" not in out            # duplicated title dropped
    assert "<strong>Bold claim.</strong>" in out          # bold parsed, not literal
    assert "**" not in out                                 # no raw markdown left
    assert out.count("<li>") == 4 and "<ol>" in out and "<ul>" in out
    assert '<p class="cont">Continuation line.</p>' in out
    assert "<blockquote><p>quoted guidance</p></blockquote>" in out
    assert "<code>read_company_file</code>" in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out  # injection stays inert
    assert "<script>" not in out


def test_kb_split_ordered_list_keeps_its_numbering():
    """core-principles runs 1-8, a bullet list, then 9-10. The resumed list must not
    restart at 1 — the browser's list-item counter follows the start attribute."""
    from build_kb_pages import render_markdown

    out = render_markdown("1. first\n2. second\n* a bullet\n9. ninth\n10. tenth")

    assert '<ol start="9">' in out                  # resumed list declares its origin
    assert out.count("<ol") == 2 and "<ul>" in out
    assert 'start="1"' not in out                    # the opening list needs no attribute


def test_kb_rail_uses_only_existing_metadata():
    """The provenance rail is derived, never invented — absent fields emit no chip."""
    from build_kb_pages import _rail

    assert _rail({}) == ""
    rail = _rail({"pp": "pp3", "section_type": "principle", "risk_level": "high",
                  "intended_user": "practitioner,manager", "confidentiality": "high"})
    assert '<a class="pp" href="../#pp3">PP3</a>' in rail  # PP jumps to its index group
    assert 'href="../t/type-principle/"' in rail           # facet items are real links
    assert 'class="hi" href="../t/risk-high/"' in rail     # high risk flagged AND linked
    assert 'href="../t/for-practitioner/"' in rail         # combined audience split into
    assert 'href="../t/for-manager/"' in rail              #   one link per user
    assert "<span>" not in rail            # nothing left that looks pressable but isn't
    assert rail.count(">risk high<") == 1  # risk shown once...
    assert rail.count(">high<") == 0       # ...and no bare confidentiality "high" label


def test_kb_facet_pages_satisfy_rail_links(tmp_path):
    """Every href the rail emits must resolve to a generated facet page listing
    the chunk — links and pages come from the same _facets() so they cannot drift."""
    import json as _json

    import build_kb_pages

    chunks = [{"id": "pp3-methods", "title": "Methods", "pp": "pp3",
               "breadcrumb": "PP3: Analysis > Methods",
               "text": "Body.", "section_type": "minimum_controls",
               "risk_level": "high", "intended_user": "practitioner,manager"}]
    src = tmp_path / "chunks.json"
    src.write_text(_json.dumps(chunks), encoding="utf-8")

    build_kb_pages.build(src, tmp_path / "kb")

    for slug in ("type-minimum-controls", "risk-high", "for-practitioner", "for-manager"):
        page = (tmp_path / "kb" / "t" / slug / "index.html").read_text(encoding="utf-8")
        assert '<a href="../../pp3-methods/">PP3: Analysis &gt; Methods</a>' in page
    assert "1 section tagged" in (tmp_path / "kb" / "t" / "risk-high" /
                                  "index.html").read_text(encoding="utf-8")


def test_update_register_entry_tool_registered():
    """Tool #12 (P4 lessons #16): field-level register patch — the only sanctioned register
    write path; its description must forbid whole-file rewrites via write_company_file."""
    import asyncio

    async def main():
        tools = await server.mcp.list_tools()
        by_name = {tool.name: tool for tool in tools}
        assert "update_register_entry" in by_name, "register patch tool not registered"
        # 15 = the original 12 + resource_dependencies (Open item 9)
        #    + update_bia_activity (2026-07-30 contract bundle)
        #    + search_company_files (2026-07-31: the agent could not answer "is this owner
        #      named anywhere else" — list+read one folder at a time was the only route)
        assert len(by_name) == 15, f"expected 15 tools, got {len(by_name)}"
        desc = by_name["update_register_entry"].description
        assert "NEVER rewrite" in desc
        assert "field" in desc.lower() and "asset_id" in desc
    asyncio.run(main())


def test_update_bia_activity_tool_registered():
    """Tool #14 (2026-07-30 contract bundle): administrative-metadata correction of the
    saved record — the allowlist, the approval params, and the re-open rule live in the
    description; the referee tool's contract names the dependencies field."""
    import asyncio

    async def main():
        tools = await server.mcp.list_tools()
        by_name = {tool.name: tool for tool in tools}
        assert "update_bia_activity" in by_name, "record correction tool not registered"
        desc = by_name["update_bia_activity"].description
        assert "owner" in desc and "approved_by" in desc and "reason" in desc
        assert "re-opening" in desc  # analytical corrections re-open the stage
        referee = by_name["validate_bia_record"].description
        assert "dependencies" in referee and "register asset ids" in referee
    asyncio.run(main())


def test_install_doc_states_the_registered_tool_count():
    """A tool added to the MCP is invisible to a published Copilot Studio agent until a maker
    hits the refresh arrow and republishes — the tool list is a tenant-side snapshot. The
    install doc is what a maker follows while doing that, so a stale count there tells them to
    stop one tool short. It went stale once: update_bia_activity shipped 2026-07-30 and the
    Teams agent still could not see it on 2026-07-31. Pin the doc to the live registration."""
    import asyncio
    import pathlib
    import re

    async def main():
        return len(await server.mcp.list_tools())
    count = asyncio.run(main())
    doc = pathlib.Path(__file__).with_name("docs") / "ms-agent-install.md"
    # \b on the left too: without it "4 tools" matches inside "14 tools" and every count
    # below the real one reads as stale.
    named = {int(n) for n in re.findall(r"\b(\d+) tools\b", doc.read_text(encoding="utf-8"))}
    assert named == {count}, (
        f"{doc.name} names {sorted(named) or 'no'} tool count(s); the server registers {count}. "
        "A maker following this doc refreshes to that number and stops, leaving every published "
        "agent one tool behind with no error anywhere.")


def test_stall_rule_is_process_scoped():
    """R-B1 (lessons #24): register-wide stall wording made the N/A branch unreachable."""
    payload = assert_result(server.next_step("run-bia", "capture-transcript", bia="slaughter"), error=False)
    flat = " ".join(payload["copy_paste_prompt"].split())
    assert "this process's dependencies" in flat
    assert "register-level referral" in flat
    assert "OWNER MISSING" in flat


def test_stage_five_names_transcripts_as_quote_source():
    """Lessons #24: the 'quotes must come from the Stage-2 capture' false blocker."""
    payload = assert_result(server.next_step("run-bia", "asset-owner-capture"), error=False)
    flat = " ".join(payload["copy_paste_prompt"].split())
    assert "never the quote source" in flat
    assert "07_Interviews" in flat


def test_protocol_requires_verification_human_line():
    """§12: the save verification's human_line must be printed verbatim. 2026-08-19: "in the
    stage card" — a save receipt is a bare turn now (Task 2), so the receipt goes in the reply,
    not behind a card the turn is not allowed to open."""
    payload = assert_result(server.next_step("run-bia", "capture-transcript", bia="slaughter"), error=False)
    flat = " ".join(payload["protocol"].split())
    assert "verification.human_line" in flat and "verbatim" in flat


def test_write_company_file_declares_expect(monkeypatch):
    """The server tool accepts and forwards the §12 expect contract + P7 I-1 save_token."""
    import graph_files as gf_mod
    seen = {}

    def fake_write(company, path, content="", user_confirmed=False, mode="create",
                   expect=None, save_token=None):
        seen["expect"] = expect
        seen["save_token"] = save_token
        return {"written": True, "path": path, "size": 1, "mode": mode}

    monkeypatch.setattr(gf_mod, "write_file", fake_write)
    assert_result(server.write_company_file("marschkamp", "output/x.md", "hi",
                                            user_confirmed=True,
                                            expect={"markers": ["a"], "min_bytes": 1}),
                  error=False)
    assert seen["expect"] == {"markers": ["a"], "min_bytes": 1}
    assert seen["save_token"] is None
    assert_result(server.write_company_file("marschkamp", "output/bia-record.json",
                                            user_confirmed=True, save_token="ab" * 16),
                  error=False)
    assert seen["save_token"] == "ab" * 16  # content omitted: write by reference


def test_resource_dependencies_tool_wires_dep_graph_answer(monkeypatch):
    """Tool #13 (Open item 9): thin wrapper — derivation stays in dep_graph.answer."""
    import dep_graph
    canned = {"asset": {"id": "KA-01"}, "human_line": "x", "deep_link": "y"}
    seen = {}

    def fake(company, asset, fetch):
        seen["args"] = (company, asset)
        import graph_files
        assert fetch is graph_files.read_file  # live reads, referee pattern
        return canned

    monkeypatch.setattr(dep_graph, "answer", fake)
    payload = assert_result(server.resource_dependencies("marschkamp", "KA-01"), error=False)
    assert payload == canned
    assert seen["args"] == ("marschkamp", "KA-01")
    monkeypatch.setattr(dep_graph, "answer",
                        lambda *a, **k: {"error": "no asset matches", "candidates": []})
    err = assert_result(server.resource_dependencies("marschkamp", "zzz"), error=True)
    assert "error" in err


def test_next_step_forwards_company_and_defaults_marschkamp(monkeypatch):
    import addendum_tools as at
    seen = {}

    def fake(journey_id, stage_id, company="marschkamp", bia=None):
        seen["args"] = (journey_id, stage_id, company, bia)
        return {"stage_id": "capture-transcript"}

    monkeypatch.setattr(at, "next_step_fn", fake)
    assert_result(server.next_step("run-bia", "scope-and-risk", bia="slaughter"), error=False)
    assert seen["args"] == ("run-bia", "scope-and-risk", "marschkamp", "slaughter")
    assert_result(server.next_step("run-bia", "scope-and-risk", company="marschkamp-demo"),
                  error=False)
    assert seen["args"][2] == "marschkamp-demo"
    assert_result(server.next_step("run-bia", "scope-and-risk", company=None), error=False)
    assert seen["args"][2] == "marschkamp"  # a client's explicit null never becomes "None"
    assert seen["args"][3] is None  # an omitted / empty bia reaches the gate as None, never ""


def test_write_tool_tells_the_agent_to_show_the_link():
    """A returned url nobody prints is the same as no url: run (a) 2026-08-18, Hans asked four times."""
    import asyncio
    async def main():
        tools = {t.name: t for t in await server.mcp.list_tools()}
        d = " ".join(tools["write_company_file"].description.split())
        assert "url" in d and "openable" in d.lower(), d[-300:]
    asyncio.run(main())


def test_write_tool_says_overwrite_is_the_version_affordance():
    """Run (a) 2026-08-18: Bruno improvised `-v1` snapshot copies twice (stage1 scope, the LF-ABP-01
    proposal) because nothing told him a corrected document simply overwrites in place and SharePoint
    keeps the earlier version. The tool says so; no new tool (ponytail: version history IS the
    affordance)."""
    import asyncio
    async def main():
        tools = {t.name: t for t in await server.mcp.list_tools()}
        d = " ".join(tools["write_company_file"].description.split())
        assert "version history" in d and "overwrites in place" in d, d[-400:]
        assert "-v1" in d and "never" in d
    asyncio.run(main())


def test_i1_schema_surface_save_token_company_contracts():
    """The deploy trio republishes THIS schema: save_token + optional content on the
    write tool, company on next_step, and the new fields in the output schemas."""
    import asyncio

    async def main():
        tools = await server.mcp.list_tools()
        by_name = {tool.name: tool for tool in tools}

        write = by_name["write_company_file"]
        assert "save_token" in write.inputSchema["properties"]
        assert "content" not in write.inputSchema.get("required", [])
        assert "save_token" in write.description
        assert "never re-type the record" in write.description

        nxt = by_name["next_step"]
        assert nxt.inputSchema["properties"]["company"].get("default") == "marschkamp"
        assert "company" not in nxt.inputSchema.get("required", [])
        # W11a: this line used to pin the banned word in place — it asserted that
        # next_step's description says "artifact". Renamed with the vocabulary rather
        # than deleted, so the description is still required to name what it verifies.
        assert "document" in nxt.description.lower()

        referee = by_name["validate_bia_record"]
        assert "save_token" in referee.description
        assert "save_token" in referee.outputSchema["properties"]
        assert "document_contracts" in nxt.outputSchema["properties"]

    asyncio.run(main())


def test_app_root_default_is_uniform_across_modules(tmp_path):
    """With BIA_WORKFLOW_ROOT unset, all three modules must resolve to their OWN directory.
    server/graph_files used to hardcode /opt/brain/ai-addendum while
    journeys.py was file-relative, so a worktree/copy run read the live tree instead of
    itself; systemd sets the variable in production and masked the split."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    here = Path(__file__).resolve().parent
    env = {k: v for k, v in os.environ.items()
           if k not in ("BIA_WORKFLOW_ROOT", "BIA_WORKFLOW_JOURNEYS_DIR", "BIA_WORKFLOW_DATA_DIR")}
    env["BIA_WORKFLOW_MCP_TOKEN"] = "test-token"
    env["PYTHONPATH"] = str(here)
    probe = (
        "import json, server, graph_files, journeys, retrieval, build_chunks, addendum_tools;"
        "print(json.dumps([str(server.APP_ROOT), str(graph_files.APP_ROOT),"
        " str(journeys._ROOT), str(journeys.JOURNEYS_DIR), str(retrieval.DEFAULT_DATA_DIR),"
        " str(build_chunks.DEFAULT_SOURCE), str(addendum_tools._DATA_DIR)]))"
    )
    # cwd is a foreign dir: proves the default is file-relative, not cwd-relative.
    out = subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, env=env,
                         capture_output=True, text=True, check=True)
    *roots, journeys_dir, data_dir, source, tools_data = json.loads(out.stdout.strip().splitlines()[-1])
    assert roots == [str(here)] * 3, f"BIA_WORKFLOW_ROOT defaults diverge: {roots}"
    # C15 (2026-08-18): the CLI/data defaults are file-relative too — no /opt/brain/ai-addendum
    assert data_dir == tools_data == str(here / "data"), (data_dir, tools_data)
    assert source == str(here / "addendum-clean.md"), source
    # C1 (2026-08-18): the journeys live in the public workflow-design repo, mounted as
    # the `design/` submodule — the default must point there, not at a journeys/ dir.
    assert journeys_dir == str(here / "design"), journeys_dir


def test_onboarding_carries_the_recording_consent_line():
    """Visibility layers B/C read what users type; the onboarding says so (2026-08-16)."""
    from instructions import INSTRUCTIONS
    line = "Conversations are recorded and reviewed to improve this workflow."
    assert line in INSTRUCTIONS


def test_start_journey_description_catches_guide_and_walk_through_openers():
    """Live 2026-08-17 14:03 CEST: "guide me through the BIA" produced a stage card from model
    priors — Copilot did initialize + ListTools only, no CallTool (journal), no call-log row.
    Same class as 13.08 "walk me thourgh a BIA!" → no journey call. The description must name
    the explain/guide/walk-through openers and forbid a self-drafted stage card."""
    import asyncio

    async def main():
        tools = await server.mcp.list_tools()
        desc = {tool.name: tool for tool in tools}["start_journey"].description or ""
        for opener in ("guide me through", "walk me through", "explain the BIA", "how does this work"):
            assert opener in desc, opener
        assert "Never present a stage card you did not get from this tool" in desc

    asyncio.run(main())


def test_stage_payload_card_counts_the_five_named_stages():
    """The stage number lives inside the name ("Stage 1 · Identification of scope") since
    2026-08-16; a separate "1/6" field invited the card "Stage 1/6 — …" that contradicted the
    deck and the protocol, so this test banned any n/total field outright.

    2026-08-19: the ban's reason held — a fraction over the six *entries* contradicts the
    deck's five — but its remedy didn't: it left no way to say "Stage 1 of 5" at all, and
    Bruno needs to say it. The new "card" field derives the denominator from the stages whose
    own label is a plain integer (five — 3a is excluded by derivation, not a hand-typed 5), so
    the payload now carries the fraction the deck actually uses instead of none at all. The
    raw "1/6" slash form stays gone; only the derived "N of 5" prose form exists, and only
    inside "card"."""
    valid = {c.id for c in server.index.chunks}
    bia = journey_engine.load_journeys(valid_chunk_ids=valid)["run-bia"]
    payload = journey_engine.render_stage_tool(bia, bia.first_stage(), 1, len(bia.stages))
    assert "stage" not in payload
    assert not any(isinstance(v, str) and v.strip() == "1/6" for v in payload.values())
    assert payload["name"] == "Stage 1 · Identification of scope"
    assert payload["card"] == "**Stage 1 of 5 · Identification of scope**"
    started = assert_result(server.start_journey("run-bia"), error=False)
    assert "stage" not in started and started["stage_id"] == "scope-and-risk"
    assert started["card"] == payload["card"]


def test_env_knobs_are_the_kept_list():
    """C13 (2026-08-18): the deploy surface is exactly these knobs — the unit sets the four
    /srv paths, tests set MCP_TOKEN, and nothing else is read from the environment. A new
    knob is a decision, not a convenience: add it here on purpose or not at all."""
    import re
    from pathlib import Path
    here = Path(__file__).resolve().parent
    seen = set()
    for py in here.glob("*.py"):
        if py.name.startswith("test_") or py.name == "conftest.py":
            continue
        seen |= set(re.findall(r"BIA_WORKFLOW_[A-Z_]+", py.read_text(encoding="utf-8")))
    assert seen == {"BIA_WORKFLOW_ROOT", "BIA_WORKFLOW_DATA_DIR", "BIA_WORKFLOW_USAGE_DIR",
                    "BIA_WORKFLOW_TOKEN_FILE", "BIA_WORKFLOW_JOURNEYS_DIR",
                    "BIA_WORKFLOW_MCP_TOKEN", "BIA_WORKFLOW_COMPANIES"}, sorted(seen)


def test_static_page_builders_read_chunks_from_the_data_dir_knob(tmp_path, monkeypatch):
    """On brain the chunks live in /srv/addendum/data (BIA_WORKFLOW_DATA_DIR), not in the
    checkout — build_guide_page hardcoded <checkout>/data/chunks.json and died in the T6
    static regeneration (2026-08-18). Both builders follow the one DATA_DIR knob."""
    import importlib
    from pathlib import Path
    data = Path(os.environ["BIA_WORKFLOW_DATA_DIR"])
    assert (data / "chunks.json").exists()
    monkeypatch.setenv("BIA_WORKFLOW_DATA_DIR", str(data))
    import build_guide_page, build_kb_pages
    importlib.reload(build_kb_pages)
    importlib.reload(build_guide_page)
    assert build_guide_page.DATA_DIR == data and build_kb_pages.DEFAULT_CHUNKS == data / "chunks.json"
    out = build_guide_page.build(tmp_path / "guide")
    assert out.exists() and "Identification of scope" in out.read_text(encoding="utf-8")


def test_stage_one_will_not_advance_before_its_documents_are_read(monkeypatch):
    """The measured failure of 2026-08-19: start_journey, then zero reads, then activities
    proposed from memory — and Hans's verdict, 'no vet, no acceptance, no unloading'."""
    import addendum_tools, graph_files
    graph_files.forget_reads()
    out = addendum_tools.next_step_fn("run-bia", "scope-and-risk", company="marschkamp", bia="slaughter")
    assert out.get("error") == "stage_incomplete", out
    assert "never read" in out["message"]
    assert "method.json" in out["message"] or "dependency-register.json" in out["message"]


def test_stage_one_advances_once_they_are_read(monkeypatch):
    """The guard against a gate that simply blocks everything."""
    import addendum_tools, graph_files
    graph_files.forget_reads()
    for path in ("02_BCM-Method/method.json", "03_Dependencies/dependency-register.json"):
        graph_files.note_read("marschkamp", path)
    out = addendum_tools.next_step_fn("run-bia", "scope-and-risk", company="marschkamp", bia="slaughter")
    assert out.get("error") != "stage_incomplete", out


def test_a_document_the_company_never_supplied_does_not_trap_the_journey(monkeypatch):
    """The failure mode a blocking gate invites. requires_reads names two files; a company
    that has not supplied one must still be able to advance, because the method's own
    instruction for that case is 'ask the user for it — never invent'. Overrides the autouse
    _advance_gate_world stub, which otherwise makes every document exist."""
    import addendum_tools, graph_files

    def missing_register(company, path):
        if path.endswith("dependency-register.json"):
            return {"error": f"file not found: {path}"}
        c = graph_files._contract_for(path) or {"markers": [], "min_bytes": 0}
        content = "\n".join(c["markers"]) + "\n" + "x" * c["min_bytes"]
        return {"path": f"{company}/{path}", "content": content,
                "size": len(content.encode("utf-8"))}

    monkeypatch.setattr(addendum_tools, "_fetch_artifact", missing_register)
    graph_files.forget_reads()
    graph_files.note_read("marschkamp", "02_BCM-Method/method.json")
    out = addendum_tools.next_step_fn("run-bia", "scope-and-risk", company="marschkamp", bia="slaughter")
    assert out.get("error") != "stage_incomplete", out


def test_a_workflow_fallback_start_does_not_wipe_the_read_credit():
    """`forget_reads` belongs to a journey that actually starts. Called before the lookup, a
    start_journey that folds into a single workflow instead — the lane an agent reaches by
    asking for one mid-run — wiped the credit for reads it really did, and stage 1 then
    refused to advance until the same two files were read again."""
    import addendum_tools, graph_files
    graph_files.forget_reads()
    graph_files.note_read("marschkamp", "02_BCM-Method/method.json")
    out = addendum_tools.start_journey_fn("no-such-journey-zzz")
    assert "stage_id" not in out, out          # folded to a workflow; no journey started
    assert "02_BCM-Method/method.json" in graph_files.reads_seen("marschkamp")


def test_every_test_starts_with_an_empty_read_store():
    """conftest clears the save-token store per test and did not clear the read store, so
    reads leaked process-wide: earlier tests in this file note marschkamp reads, and a
    later stage-1 gate test could inherit that credit and silently stop testing the gate.
    The fixture owes both stores the same isolation."""
    import graph_files
    assert graph_files.reads_seen("marschkamp") == set()


def test_the_risk_level_does_not_depend_on_how_the_model_phrases_the_task():
    """Live 2026-08-20: three runs of the same stage 1 produced three governance answers —
    Buzz said medium then high, Teams run 1 never showed a level, Teams run 2 said High. The
    cause is that `risk_level` is the max risk of the top-4 chunks retrieved for a
    task_description the MODEL writes, so the classification tracked the phrasing rather than
    the work: `list files` scored high while `BIA preparation` scored medium and `scoping`
    scored low. That level goes into the header of the document a human signs.

    While a journey is running, the journey's own pinned description is authoritative — the
    stage knows what it is, the model does not need to be trusted to describe it."""
    import addendum_tools
    addendum_tools.start_journey_fn("run-bia")
    try:
        levels = {addendum_tools.identify_ai_risks_fn(t)["risk_level"]
                  for t in ("scoping", "list files", "BIA preparation",
                            "draft the interview guide for packing")}
        assert len(levels) == 1, f"same journey, four phrasings, {len(levels)} answers: {levels}"
    finally:
        addendum_tools.forget_risk_task()


def test_the_advance_gate_names_every_unread_source_at_once(monkeypatch):
    """The advance gate has the same one-at-a-time shape the write jaw had, and it produced the
    same symptom on 2026-08-20 09:32: `next_step` rejected naming one file, the agent read it,
    retried, and was rejected again for the next. Round trips scaled with the number of
    required reads. Both jaws name every unread source now."""
    import addendum_tools, graph_files, journeys as je
    stage = je.load_journeys()["run-bia"].first_stage()
    assert len(stage.requires_reads) >= 2, "fixture needs a stage with two required reads"
    monkeypatch.setattr(addendum_tools, "_fetch_artifact",
                        lambda company, path: {"content": "x" * 4000})
    graph_files.forget_reads()
    out = addendum_tools.next_step_fn("run-bia", stage.id, company="marschkamp", bia="logistics")
    assert out.get("error") == "stage_incomplete", out
    import json as _json
    blob = _json.dumps(out)
    for path in stage.requires_reads:
        assert path in blob, f"{path} was not named in the rejection"
