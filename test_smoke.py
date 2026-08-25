#!/usr/bin/env python3
"""Smoke tests for the AI Addendum MCP implementation."""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("BIA_WORKFLOW_MCP_TOKEN", "test-token")

import server  # noqa: E402
import journeys as journey_engine  # noqa: E402


def _rooms_block(text):
    """The demo-rooms location block, sliced at its own closing brace. The nested dotfile deny
    is a one-liner at indent 8, so the first `\\n    }` after the opener is the block's own."""
    i = text.index("location ^~ /demo/rooms/")
    return text[i:text.index("\n    }", i) + 6]


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
        for name in ("search", "fetch"):
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
    server.list_company_files(company="marschkamp", subpath="07_Interviews")
    assert captured["args"] == ("marschkamp", "07_Interviews")
    server.list_company_files(company="marschkamp")  # root listing still works (subpath optional)
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


def test_start_journey_schema_carries_an_optional_company_param():
    """§A.17's schema half: the manifest must offer `company` so a room agent can route the
    stage-1 digest to the tester's own room — and it must stay OPTIONAL, because the
    un-republished manifest (§A.3) keeps calling without it until a maker republishes."""
    import asyncio

    async def main():
        tools = {t.name: t for t in await server.mcp.list_tools()}
        schema = tools["start_journey"].inputSchema
        assert "company" in schema.get("properties", {}), "start_journey has no company param"
        assert "company" not in (schema.get("required") or []), "company must stay optional"
        # The wording half (brainstormed 2026-08-25): positive, and anchored to the routing
        # rule the agents already follow — not a new rule to learn, the same value they
        # already pass everywhere else. Prohibition-shaped variants lose (§B pattern).
        desc = tools["start_journey"].description or ""
        assert "same value you pass to the other company tools" in desc

    asyncio.run(main())


def test_the_three_mutating_tools_declare_destructive_annotations():
    """F5 (2026-08-25, bundled into §A.17's schema commit): every read-only tool declares
    READ_ONLY, but the three mutating tools shipped with NO annotations at all — the client
    is left to guess, and MCP's documented default for an undeclared destructiveHint is
    `true` only when readOnlyHint is also declared false, which none of them said either.
    Declare what they are: destructive (they replace bytes/fields in place) and
    non-idempotent (update_bia_activity refuses a no-op re-save by design; a repeated
    write_company_file banks a new version each time)."""
    import asyncio

    async def main():
        tools = {t.name: t for t in await server.mcp.list_tools()}
        for name in ("write_company_file", "update_register_entry", "update_bia_activity"):
            ann = tools[name].annotations
            assert ann is not None, f"{name} declares no annotations"
            assert ann.readOnlyHint is False, name
            assert ann.destructiveHint is True, name
            assert ann.idempotentHint is False, name
            assert ann.openWorldHint is False, name

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
    }
    assert expected <= set(js)
    assert "draft-plan" not in js  # retired 2026-08-24 — deferred since birth, never advertised
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
    assert "plan-reviewer" not in personas  # retired 2026-08-24 with draft-plan
    assert "department-desk" not in personas
    assert "exercise-designer" not in personas
    assert "tool-selection-adviser" not in personas


def test_render_stage_tool():
    valid = {c.id for c in server.index.chunks}
    bia = journey_engine.load_journeys(valid_chunk_ids=valid)["run-bia"]
    s = bia.first_stage()
    payload = journey_engine.render_stage_tool(bia, s, 1, len(bia.stages))
    assert payload["stage_id"] == "scope-and-risk"
    assert payload["approval_gate"]
    # the header names the stage (2026-08-16) and the advance call names the per-BIA folder
    # (2026-08-18) — asserted on the tool payload, the one surface a client actually reads
    assert "Stage 1 · Identification of scope" in payload["name"]
    assert "next_step('run-bia', 'scope-and-risk', bia='<bia>')" in payload["advance"]
    # expected_output stays yaml-side since 2026-08-24 (the prompt surface that rendered it
    # is gone); the yaml still carries a non-empty one for the author and reviewer
    assert s.expected_output


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
        server.write_company_file(company="marschkamp", path="output/x.md", content="hi",
                                  user_confirmed=False),
        error=True,
    )
    assert "approval" in payload["error"].lower()


def test_the_prompt_and_resource_surface_stays_retired():
    """C7/C8, executed 2026-08-24: the MCP prompt `run_bia` and the three `addendum://`
    resources served no client — Copilot Studio wires tools only and the install doc never
    named them. They are gone, and their return must be deliberate: re-registering either
    surface is a Copilot manifest republish (§A.3), and it would revive four backing
    functions and a second stage renderer with them."""
    import asyncio

    async def main():
        assert await server.mcp.list_prompts() == []
        assert await server.mcp.list_resources() == []
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
    """2026-08-19 payload budget: the checklist stays on the yaml Stage object; the stage
    tool payload carries only what the turn needs."""
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
    # "no fixed question count" was superseded 2026-08-24 by Hans's band ("at most twelve
    # questions per activity") when the guide prescription landed with the guide jaw.
    assert "at most twelve questions" in flat
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


def test_transport_allows_only_the_new_public_host(mcp_http):
    """agent.ai4bcm.org is the MCP; addendum.aibcm.org is retired (owner ruling 2026-08-24 —
    "the new MCP is agent.ai4bcm.org … this is a decision"). The old name must now fail
    DNS-rebinding protection like any other wrong Host, so a stale client gets a loud 421,
    not silent service on a name the estate believes is gone.

    The bearer check runs first, so a wrong Host answers 421 only *after* a valid token —
    a bare `curl /mcp` looks alive (401) even when the process never picked up the change.
    """
    allowed = server.mcp.settings.transport_security.allowed_hosts
    assert "agent.ai4bcm.org" in allowed
    assert "addendum.aibcm.org" not in allowed

    client = mcp_http
    hdrs = {**_MCP_ACCEPT, "Authorization": "Bearer test-token"}
    ok = client.post("/mcp", json=_MCP_BODY, headers={**hdrs, "Host": "agent.ai4bcm.org"})
    assert ok.status_code == 200, ok.text
    assert "tools" in ok.json()["result"]
    old = client.post("/mcp", json=_MCP_BODY, headers={**hdrs, "Host": "addendum.aibcm.org"})
    assert old.status_code == 421, old.text
    evil = client.post("/mcp", json=_MCP_BODY, headers={**hdrs, "Host": "evil.example"})
    assert evil.status_code == 421, evil.text


def test_list_topics_is_retired():
    """Zero calls in every recorded week (W33–W35, 753 logged rows) — removed 2026-08-24 by
    owner ruling with the ponytail-audit; browsing rides search/fetch. If registration ever
    comes back, a maker must refresh-and-republish the Copilot manifest (§A.3) — this pin
    makes the return deliberate, never accidental."""
    import asyncio

    async def main():
        return [t.name for t in await server.mcp.list_tools()]
    names = asyncio.run(main())
    assert "list_topics" not in names
    assert "search" in names and "fetch" in names


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
        # 14 = the original 12 + resource_dependencies (Open item 9)
        #    + update_bia_activity (2026-07-30 contract bundle)
        #    + search_company_files (2026-07-31: the agent could not answer "is this owner
        #      named anywhere else" — list+read one folder at a time was the only route)
        #    - list_topics (2026-08-24: zero calls in every recorded week; owner ruling)
        assert len(by_name) == 14, f"expected 14 tools, got {len(by_name)}"
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
    assert_result(server.write_company_file(company="marschkamp", path="output/x.md", content="hi",
                                            user_confirmed=True,
                                            expect={"markers": ["a"], "min_bytes": 1}),
                  error=False)
    assert seen["expect"] == {"markers": ["a"], "min_bytes": 1}
    assert seen["save_token"] is None
    assert_result(server.write_company_file(company="marschkamp", path="output/bia-record.json",
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
    payload = assert_result(server.resource_dependencies(company="marschkamp", asset="KA-01"), error=False)
    assert payload == canned
    assert seen["args"] == ("marschkamp", "KA-01")
    monkeypatch.setattr(dep_graph, "answer",
                        lambda *a, **k: {"error": "no asset matches", "candidates": []})
    err = assert_result(server.resource_dependencies(company="marschkamp", asset="zzz"), error=True)
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
        # §A.12: `record` is optional so the referee can judge the file on disk. A required
        # argument is a question Copilot slot-fills, and the answer it filled on 2026-08-24
        # was a 152-byte stub.
        assert "record" not in referee.inputSchema.get("required", [])
        assert "omit" in referee.description.lower()
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


_DIGEST_METHOD = json.dumps({
    "version": "2026.1-MK",
    "scenarios": [{"id": "financial", "name": "Financial"},
                  {"id": "animal-food", "name": "Animal welfare / food safety"}],
    "time_horizons": ["0-4 h", "4-8 h", "8-24 h", "1-3 d", "3-7 d", "1 week"],
    "intolerability_threshold": 4,
})
_DIGEST_REGISTER = json.dumps({
    "KA-01": {"name": "Central refrigeration", "criticality": "high", "mtpd": "8 h",
              "rto": "4 h",
              "consumers": [{"dept": "logistics", "activity": "Cold-store dispatch",
                             "consumer_mtpd": "8 h"}]},
    "IT-ERP-01": {"name": "SAP S/4HANA", "criticality": "high", "mtpd": "8 h", "rto": "8 h",
                  "consumers": [{"dept": "sales", "activity": "Customer order processing",
                                 "consumer_mtpd": "8 h"},
                                {"dept": "logistics", "activity": "Cold-store dispatch"}]},
})


def _digest_world(monkeypatch):
    """A JSON-shaped company: both stage-1 sources parse, so the digest can build. The
    conftest default stub serves marker-text (not JSON) on purpose — that keeps every
    existing gate test testing the gate."""
    import addendum_tools

    def fetch(company, path):
        if path.endswith("method.json"):
            return {"path": f"{company}/{path}", "content": _DIGEST_METHOD}
        if path.endswith("dependency-register.json"):
            return {"path": f"{company}/{path}", "content": _DIGEST_REGISTER}
        return {"error": f"file not found: {path}"}

    monkeypatch.setattr(addendum_tools, "_fetch_artifact", fetch)


def test_start_journey_serves_stage_one_data_and_grants_read_credit(monkeypatch):
    """Backlog §B.9, built after the 2026-08-22 live run: the model drafts before reading
    (six instructions failed to change that), so the server serves the material instead —
    the register's per-department activities WITH tier and MTPD (the ranking data stage 1's
    own next_moves demand) and the method parameters, inside the stage-1 payload. Credit is
    granted because the content demonstrably reached the model — the same standard that
    excludes referee-internal reads."""
    import addendum_tools, graph_files
    _digest_world(monkeypatch)
    graph_files.forget_reads()
    out = addendum_tools.start_journey_fn("run-bia")
    assert out.get("stage_id") == "scope-and-risk", out
    assert graph_files.reads_seen("marschkamp") == {
        "02_BCM-Method/method.json", "03_Dependencies/dependency-register.json"}
    brief = out["copy_paste_prompt"]
    assert "Customer order processing" in brief        # a register activity, by name
    assert "sales" in brief and "logistics" in brief   # grouped by department
    assert "high" in brief and "8 h" in brief          # tier + MTPD — the ranking data
    assert "2026.1-MK" in brief                        # method version
    assert "intolera" in brief.lower() or "4" in brief # the threshold reached the model


def test_start_journey_data_failure_grants_no_credit_and_still_starts(monkeypatch):
    """Graph down at journey start: the stage must still open (the run is not held hostage
    to a data blip) but nothing is credited — the write jaw then protects exactly as today."""
    import addendum_tools, graph_files
    import httpx as _httpx

    def down(company, path):
        raise _httpx.ConnectError("graph unreachable")

    monkeypatch.setattr(addendum_tools, "_fetch_artifact", down)
    graph_files.forget_reads()
    out = addendum_tools.start_journey_fn("run-bia")
    assert out.get("stage_id") == "scope-and-risk", out
    assert graph_files.reads_seen("marschkamp") == set()


def test_the_default_test_world_grants_no_credit():
    """The conftest stub returns marker-text, not JSON — the digest must fail to build there
    and grant nothing, or every existing gate test would silently stop testing the gate."""
    import addendum_tools, graph_files
    graph_files.forget_reads()
    out = addendum_tools.start_journey_fn("run-bia")
    assert out.get("stage_id") == "scope-and-risk", out
    assert graph_files.reads_seen("marschkamp") == set()


def test_digest_is_capped_and_a_dropped_register_grants_no_register_credit(monkeypatch):
    """Adversarial-review amendment 2: truncation fails toward no-credit. When the cap
    cannot fit the register's lines, the register path earns no credit — credit for
    unserved material is the exact dishonesty the credit-at-serve rationale forbids."""
    import addendum_tools, graph_files
    _digest_world(monkeypatch)
    monkeypatch.setattr(addendum_tools, "DIGEST_MAX_CHARS", 260)  # method fits, register not
    graph_files.forget_reads()
    out = addendum_tools.start_journey_fn("run-bia")
    seen = graph_files.reads_seen("marschkamp")
    assert "02_BCM-Method/method.json" in seen
    assert "03_Dependencies/dependency-register.json" not in seen
    assert "Customer order processing" not in out["copy_paste_prompt"]


def test_digest_fits_the_cap_at_the_real_registers_scale(monkeypatch):
    """Caught 2026-08-23 by running the builder against the archived real register BEFORE the
    Teams run did: 33 activities with long asset names and free-prose MTPDs produced 5,687
    chars against the 2,000 cap — so the fail-closed rule dropped the register and the
    deployed fix silently degraded to the old behavior on the live company. This fixture
    mirrors the real register's statistics (6 departments, 33 consumers, verbose names and
    clock prose); the digest must fit the cap WITH the register credited, keep every activity
    name, and keep tier and a short MTPD clock where one is recorded."""
    import addendum_tools, graph_files
    depts = ["kuehlung-lager-versand", "schlachtung", "zerlegung-verpackung",
             "verwaltung-vertrieb", "technik-instandhaltung", "all-departments"]
    register = {}
    for i in range(33):
        register[f"AS-{i:02d}"] = {
            "name": f"Central refrigeration plant NH3/CO2 cascade, compressors 1-3 (N+1), room {i}",
            "criticality": (i % 2) + 1,
            "consumers": [{"dept": depts[i % 6],
                           # ~36 chars — the real register's average activity-name length
                           "activity": f"frozen storage of product line {i}",
                           "consumer_mtpd": f"cold-chain and welfare clocks open within {i % 9 + 1} h of an outage (analyst note)"}],
        }
    world = {"02_BCM-Method/method.json": _DIGEST_METHOD,
             "03_Dependencies/dependency-register.json": json.dumps(register)}
    monkeypatch.setattr(addendum_tools, "_fetch_artifact",
                        lambda company, path: {"content": world[path]} if path in world
                        else {"error": "not found"})
    graph_files.forget_reads()
    out = addendum_tools.start_journey_fn("run-bia")
    assert "03_Dependencies/dependency-register.json" in graph_files.reads_seen("marschkamp"), \
        "the register must be credited at real-register scale — a dropped register is the bug"
    brief = out["copy_paste_prompt"]
    digest = brief[brief.index("\n\nCompany data for this stage"):]
    assert len(digest) <= addendum_tools.DIGEST_MAX_CHARS        # all-inclusive — headers count
    assert "line 7" in digest and "line 32" in digest            # no activity silently dropped
    assert "tier" in digest and "8 h" in digest                  # ranking data survives


# --- §A.17 (2026-08-25): the §B.9 digest must follow the room, not the default ---------
# Room world on real room disk: _fetch_artifact is restored to graph_files.read_file so the
# digest routes through the SAME seam every other tool uses — a room company reads local
# disk, an allowlisted company would read Graph (no test here names one).

_ROOM_METHOD = json.dumps({
    "version": "2026.1-MK",
    "scenarios": [{"id": "financial", "name": "Financial"}],
    "time_horizons": ["0–4 h", "8 h", "24 h", "48 h", "72 h", "1 week"],
    "intolerability_threshold": 4,
})
_ROOM_REGISTER = json.dumps({
    "IT-ERP-01": {"name": "SAP S/4HANA", "criticality": 1,
                  "consumers": [{"dept": "logistik",
                                 "activity": "room-only cold-store dispatch",
                                 "consumer_mtpd": "8 h"}]},
})


def _seed_room(code):
    import graph_files
    room = graph_files.ROOMS_DIR / code
    (room / "02_BCM-Method").mkdir(parents=True)
    (room / "03_Dependencies").mkdir(parents=True)
    (room / "02_BCM-Method" / "method.json").write_text(_ROOM_METHOD, encoding="utf-8")
    (room / "03_Dependencies" / "dependency-register.json").write_text(
        _ROOM_REGISTER, encoding="utf-8")
    return room


def test_a_room_company_start_journey_digests_the_room_copy_and_credits_the_room(monkeypatch):
    """Backlog §A.17, root-caused 2026-08-25: start_journey took no company, so the §B.9
    digest was built and read-credited for marschkamp whatever room the user named — every
    fresh room deterministically paid the ~3-press read-refusal round §B.9 exists to remove,
    and the digest bytes came from live Graph canon instead of the room (wrong data plane, a
    tenant round-trip per QR scan). A room company must route BOTH the content and the
    credit to the room."""
    import addendum_tools, graph_files
    monkeypatch.setattr(addendum_tools, "_fetch_artifact", graph_files.read_file)
    _seed_room("bia7")
    graph_files.forget_reads()
    out = addendum_tools.start_journey_fn("run-bia", company="bia7")
    assert out.get("stage_id") == "scope-and-risk", out
    assert "room-only cold-store dispatch" in out["copy_paste_prompt"]  # room bytes, not canon
    assert graph_files.reads_seen("bia7") == {
        "02_BCM-Method/method.json", "03_Dependencies/dependency-register.json"}
    assert graph_files.reads_seen("marschkamp") == set()   # no credit leaks to the default


def test_a_room_start_journey_unlocks_the_stage1_write_gate_in_that_room(monkeypatch):
    """The defect as the tester feels it: the stage-1 save was refused in a fresh room for
    the very sources the server had just served (06:51:30Z, problem 1 verbatim). After a
    room-company start_journey the stage-1 write must pass the read gate and land on the
    room's own disk."""
    import addendum_tools, graph_files
    monkeypatch.setattr(addendum_tools, "_fetch_artifact", graph_files.read_file)
    _seed_room("bia8")
    graph_files.forget_reads()
    addendum_tools.start_journey_fn("run-bia", company="bia8")
    guide = (
        "## Scope\n" + "the logistik department scope line\n" * 30 +
        "## Risk and environment\n" + "the risk line\n" * 10 +
        "## Method parameters\nHorizons: 0–4 h, 8 h, 24 h, 48 h, 72 h, 1 week.\n" +
        "## Interview guide\n"
        "### room-only cold-store dispatch\n"
        "1. If this stops, what breaks at 0–4 h, 8 h, 24 h, 48 h, 72 h, 1 week?\n"
        "2. When is it intolerable, and in which categories?\n"
        "3. Nights, weekends, peak weeks — what changes?\n"
        "4. People per shift, rooms, kit — what must keep running?\n"
        "5. This depends on IT-ERP-01 — what happens while it is away?\n"
        "6. Single points of failure? (Recorded for the risk assessment, not scored here.)\n"
        "### Short version (20 minutes)\n"
        "1. What breaks first? 2. What can you not tolerate? 3. What do you depend on?\n"
        "### Bring to the interview\n- last year's BIA for logistik\n- the delivery SLA\n")
    out = graph_files.write_file(
        "bia8", "output/logistik/stage1-scope-and-guide.md", guide,
        user_confirmed=True,
        expect={"markers": ["## Scope", "## Risk and environment", "## Method parameters",
                            "## Interview guide"], "min_bytes": 1200})
    assert out.get("written") is True, out
    assert (graph_files.ROOMS_DIR / "bia8" / "output" / "logistik" /
            "stage1-scope-and-guide.md").is_file()


def test_advancing_past_a_pre_existing_stage1_document_names_it_and_offers_the_choice(monkeypatch):
    """Backlog §A.16, found the expensive way 2026-08-24 22:26: a re-run in a used room walked
    from the scope card straight to Stage 2 — the gate was RIGHT (the document exists) but the
    agent never said so, and the owner read it as 'the deploy destroyed the questionnaire
    process'. The server cannot tell 'just saved' from 'left over' (no session id — settled),
    but the model can, from the conversation: so the payload hands it the facts — what was
    found, how big, when saved — and a positive conditional with the clean-run choice."""
    import addendum_tools, graph_files
    monkeypatch.setattr(addendum_tools, "_fetch_artifact", graph_files.read_file)
    room = _seed_room("bia9")
    doc = ("## Scope\n## Risk and environment\n## Method parameters\n## Interview guide\n"
           + "x" * 3200)
    (room / "output" / "packaging").mkdir(parents=True)
    (room / "output" / "packaging" / "stage1-scope-and-guide.md").write_text(
        doc, encoding="utf-8")
    graph_files.forget_reads()
    addendum_tools.start_journey_fn("run-bia", company="bia9")  # what a re-run's first press does
    out = addendum_tools.next_step_fn("run-bia", "scope-and-risk", company="bia9",
                                      bia="packaging")
    assert out.get("stage_id") == "capture-transcript", out     # the gate still advances (I-5)
    info = out.get("already_saved")
    assert info, "a found finished document must be narrated, never skipped silently"
    docs = info["documents"]
    assert docs[0]["path"] == "output/packaging/stage1-scope-and-guide.md"
    assert docs[0]["size"] == len(doc.encode("utf-8"))
    assert docs[0]["saved"], "when it was saved is the fact the narration needs"
    note = info["note"]
    assert "in this conversation" in note and "carry on" in note
    assert "redo the stage" in note and "another process" in note


def test_a_later_stage_advance_carries_no_already_saved_note():
    """§A.16 is scoped to the first-stage advance — the turn every re-run walks through and
    the one the incident named. Later advances keep their exact payload shape: stage 4's
    card renders 140 chars under the 14,500 anchor, so a note on EVERY advance would breach
    the same budget the digest arithmetic protects."""
    import addendum_tools, graph_files
    graph_files.forget_reads()
    out = addendum_tools.next_step_fn("run-bia", "capture-transcript", company="marschkamp",
                                      bia="slaughter")
    assert out.get("stage_id") == "analyse-transcript", out
    assert "already_saved" not in out


def test_start_journey_without_company_is_the_marschkamp_lane_unchanged(monkeypatch):
    """The deploy-safety half of §A.17: the parameter is optional and its absence is EXACTLY
    today's behaviour — same payload, same credit — so the server deploys before any
    republish and the un-republished manifest (§A.3) keeps working."""
    import addendum_tools, graph_files
    _digest_world(monkeypatch)
    graph_files.forget_reads()
    bare = addendum_tools.start_journey_fn("run-bia")
    graph_files.forget_reads()
    named = addendum_tools.start_journey_fn("run-bia", company="marschkamp")
    assert bare == named
    assert graph_files.reads_seen("marschkamp") == {
        "02_BCM-Method/method.json", "03_Dependencies/dependency-register.json"}


def test_a_matched_start_journey_also_does_not_wipe_the_read_credit():
    """The proven defect (backlog §A.2): a matched start_journey call — the common case,
    since run-bia always matches — used to call forget_reads() and erase credit for reads
    the same run made minutes earlier. Live: 16:07:52 read the register, 16:11:49
    start_journey wiped it, 16:13:48 the write was refused for a file it had already read."""
    import addendum_tools, graph_files
    graph_files.forget_reads()
    graph_files.note_read("marschkamp", "02_BCM-Method/method.json")
    out = addendum_tools.start_journey_fn("run-bia")
    assert out.get("stage_id") == "scope-and-risk", out   # matched and returned stage 1
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


def test_no_tool_requires_a_company_while_the_allowlist_has_one_entry():
    """The schema is what Copilot Studio slot-fills on. 2026-08-20T14:37:49Z it stopped a run to
    ask "Please provide the name of the company you want to fetch a document for" — one legal
    answer, and no tool call reached the server, so only the declared schema can have caused it.

    A parameter marked required is a question. With one allowlisted company there is no question,
    so no tool may declare `company` required. `next_step` already did this (as a hardcoded
    literal); this pins it for every tool and derives the value instead."""
    import asyncio

    async def main():
        tools = await server.mcp.list_tools()
        offenders = []
        for t in tools:
            schema = t.inputSchema or {}
            if "company" in (schema.get("properties") or {}) \
                    and "company" in (schema.get("required") or []):
                offenders.append(t.name)
        assert not offenders, (
            f"these tools still make the one legal company a required question: {offenders}")

    asyncio.run(main())


def test_demo_rooms_nginx_secrecy_model():
    """T5 (2026-08-24): the rooms URL space is secret-by-code. Root-404 plus dotpath-deny
    ARE the secrecy model — and the dot-deny must be NESTED inside the rooms location,
    because ^~ suppresses server-level regex locations for everything it matches."""
    from pathlib import Path
    text = (Path(__file__).resolve().parent / "deploy" /
            "nginx-agent-ai4bcm.conf").read_text(encoding="utf-8")
    i_root = text.index("location = /demo/rooms/ { return 404; }")
    i_block = text.index("location ^~ /demo/rooms/")
    i_alias = text.index("alias /srv/addendum/demo-rooms/;", i_block)
    i_dot = text.index(r"location ~ /\. { return 404; }", i_block)
    assert i_root < i_block < i_alias < i_dot
    assert text.count("autoindex on;") == 1, "per-room listing is the estate's ONLY autoindex"
    assert text.index("autoindex on;") > i_block


def test_embed_personal_links_swap_the_marschkamp_doors():
    """Onboarding (2026-08-24 evening): a cohort manager gets ONE personal URL —
    /demo/live-<hash>/?room=<code> — and the page does the rest: the room prompt is filled
    (nothing to hand-edit), the files line becomes the link it names, and the two marschkamp
    doors (SharePoint files, dependency graph) swap to the visitor's own room. The room param
    is slug-jailed client-side and written with textContent only, so a hostile param is inert.
    Static defaults stay the brand room — with no ?room= the page is exactly the old page."""
    from pathlib import Path
    html = (Path(__file__).resolve().parent / "deploy" /
            "bia-live-embed.html").read_text(encoding="utf-8")
    assert "URLSearchParams" in html and "'room'" in html
    assert "[a-z0-9]+(?:-[a-z0-9]+)*" in html          # the client-side slug jail
    assert "innerHTML" not in html                      # room text lands via textContent only
    assert 'id="fileslink"' in html and 'id="graphlink"' in html
    assert 'href="/demo/graph/marschkamp/"' in html     # static default: the brand room
    assert "kgerner.sharepoint.com" in html             # static default: the brand share
    assert "/demo/rooms/" in html                       # the room files URL pattern exists
    # 2026-08-25: the context header named the brand room on every personal page, so a bia3
    # tester read "MARSCHKAMP" while working in bia3 — and that header is the one place the
    # support protocol ("which room are you?") can be answered at a glance.
    assert 'id="roomname"' in html
    assert "document.getElementById('roomname').textContent = 'Room ' + room;" in html
    # 2026-08-25 custom canvas: the chat is ours now — no cross-origin iframe left.
    assert "<iframe" not in html
    assert 'id="webchat"' in html


def test_embed_canvas_connects_via_token_endpoint_not_iframe():
    """Custom canvas (spec 2026-08-25): token fetch -> Direct Line -> renderWebChat, all
    first-party — which is what removes the Firefox/ETP blank frame the escape hatch was
    built for. Falsified before building: the token endpoint answers 200 with
    access-control-allow-origin:* and its token opens a conversation on the DEFAULT
    directline gateway (201), so there is deliberately no regional-domain fetch."""
    from pathlib import Path
    html = (Path(__file__).resolve().parent / "deploy" /
            "bia-live-embed.html").read_text(encoding="utf-8")
    assert "cdn.botframework.com/botframework-webchat/latest/webchat.js" in html
    assert "TOKEN_ENDPOINT" in html and "directline/token" in html
    assert "createDirectLine" in html and "renderWebChat" in html
    # The regional lookup IS required, and the first probe was over-read: POSTing to the
    # DEFAULT gateway returned 201, which proves a conversation was created — not that the
    # bot is reachable on it. Live 2026-08-25 the browser showed "Unable to connect" until
    # the domain came from the TENANT host's regionalchannelsettings
    # (https://europe.directline.botframework.com/), where the bot answers in ~16 s.
    # The earlier probe used a generic hostname from the doc sample, which has no DNS.
    assert "regionalchannelsettings" in html
    assert "channelUrlsById" in html
    assert "DIRECT_LINE/CONNECT_FULFILLED" in html


def test_embed_auto_sends_the_room_prompt_and_only_the_room_prompt():
    """§A.18: the room binding lived only in prompt text and died with the conversation —
    a tester's second chat wrote into marschkamp (live 2026-08-25 09:50:30Z). So the PAGE
    speaks first. The auto-sent text must be built from the JAILED room variable, and the
    brand page must not auto-send: with no room, connect dispatches only the greeting."""
    from pathlib import Path
    html = (Path(__file__).resolve().parent / "deploy" /
            "bia-live-embed.html").read_text(encoding="utf-8")
    assert "let roomPrompt = null;" in html
    i_jail = html.index("if (/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(room)")
    i_assign = html.index('''roomPrompt = "I'm the BC manager, room " + room +''')
    assert i_assign > i_jail                    # assigned only after the jail passed
    # The canned greeting ("Hello, I'm BIA-Workflow (public)…") is OURS to trigger — it comes
    # from the startConversation event this store dispatches, not from Direct Line. On a room
    # page it is dead weight in front of Hans's opening question, so it fires only where
    # nothing is auto-sent: the brand page, which would otherwise open to an empty transcript.
    i_prompt = html.index("if (roomPrompt) {")
    i_greet = html.index("startConversation")
    assert i_greet > i_prompt, "the greeting must be the else-branch, not unconditional"
    assert "} else {" in html[i_prompt:i_greet]
    assert "if (roomPrompt)" in html            # the send is conditional on a bound room
    assert html.count("WEB_CHAT/SEND_MESSAGE") == 1


def test_embed_canvas_normalises_inline_bullet_runs_into_lists():
    """F6's last element, witnessed 2026-08-25 on the first canvas run: block spacing and
    one-option-per-line both took, but the model writes activity lists as INLINE glyph runs
    ('• a • b • c' in one paragraph) — a shape no conduct rule covers, and the payload has 7
    chars of headroom left to teach it with. We own the canvas now, so this one is
    deterministic: the store's INCOMING_ACTIVITY hook rewrites bot text so every inline
    ' • ' run becomes a real markdown list before rendering. Display layer only — the
    transcript the server logs is untouched; user messages are never rewritten."""
    from pathlib import Path
    html = (Path(__file__).resolve().parent / "deploy" /
            "bia-live-embed.html").read_text(encoding="utf-8")
    assert "DIRECT_LINE/INCOMING_ACTIVITY" in html
    assert "role === 'bot'" in html                  # bot text only, never the user's
    needle = "replace(/" + chr(92) + "s*\u2022" + chr(92) + "s+/g, '" + chr(92) + "n- ')"
    assert needle in html                       # the glyph run becomes a list


# ── the QR claim lane (2026-08-25): one QR on the event wall, 40 simultaneous scans ──────


@pytest.fixture()
def claim_rooms(tmp_path, monkeypatch):
    """Three pristine biaN rooms plus the operator-written embed-base file. ROOMS_DIR is
    request-time state in the handler, so a plain attribute patch reaches the live app."""
    rooms = tmp_path / "demo-rooms"
    for i in (1, 2, 3):
        (rooms / f"bia{i}").mkdir(parents=True)
    (tmp_path / "embed-base").write_text(
        "https://agent.ai4bcm.org/demo/live-x/\n", encoding="utf-8")
    monkeypatch.setattr(server.graph_files, "ROOMS_DIR", rooms)
    return rooms


def test_claim_hands_out_rooms_in_order_and_sets_the_cookie(mcp_http, claim_rooms):
    mcp_http.cookies.clear()
    r1 = mcp_http.get("/demo/claim", follow_redirects=False)
    assert r1.status_code == 302
    assert r1.headers["location"] == "https://agent.ai4bcm.org/demo/live-x/?room=bia1"
    assert "bia_room=bia1" in r1.headers.get("set-cookie", "")
    mcp_http.cookies.clear()
    r2 = mcp_http.get("/demo/claim", follow_redirects=False)
    assert r2.headers["location"].endswith("?room=bia2")
    rows = (claim_rooms / ".claims.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert [json.loads(x)["room"] for x in rows] == ["bia1", "bia2"]


def test_claim_cookie_re_scan_returns_the_same_room(mcp_http, claim_rooms):
    """A phone that re-scans the QR (or reloads the claim URL) must NOT burn a second
    room — the cookie makes the claim idempotent per device."""
    mcp_http.cookies.clear()
    first = mcp_http.get("/demo/claim", follow_redirects=False)
    again = mcp_http.get("/demo/claim", follow_redirects=False)
    assert again.headers["location"] == first.headers["location"]
    rows = (claim_rooms / ".claims.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 1


def test_claim_hostile_cookie_cannot_traverse_or_block(mcp_http, claim_rooms):
    """The cookie names a filesystem path component — a forged value must neither
    traverse nor break the claim; it falls through to a fresh assignment."""
    mcp_http.cookies.clear()
    mcp_http.cookies.set("bia_room", "../../etc")
    r = mcp_http.get("/demo/claim", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].endswith("?room=bia1")


def test_claim_exhausted_says_so_instead_of_erroring(mcp_http, claim_rooms):
    for _ in range(3):
        mcp_http.cookies.clear()
        assert mcp_http.get("/demo/claim", follow_redirects=False).status_code == 302
    mcp_http.cookies.clear()
    r = mcp_http.get("/demo/claim", follow_redirects=False)
    assert r.status_code == 200
    assert "taken" in r.text


def test_claim_without_embed_base_fails_legibly(mcp_http, claim_rooms, tmp_path):
    (tmp_path / "embed-base").unlink()
    mcp_http.cookies.clear()
    r = mcp_http.get("/demo/claim", follow_redirects=False)
    assert r.status_code == 503
    assert "embed-base" in r.text


def test_claim_hands_out_a_bird_coded_room_and_skips_a_reserved_one(mcp_http, tmp_path,
                                                                   monkeypatch):
    """§A.20 (owner ruling 2026-08-25): codes go back to unguessable bird names. Sequential
    biaN was chosen so a tester could say "I am bia7" out loud and TYPE it — since the custom
    canvas the page types it for them, so the only thing sequential still buys is that a
    neighbour's room is yours ±1. The claim lane must therefore hand out slug-shaped rooms of
    any shape, and `int(name[3:])` ordering would simply crash on one.

    A room carrying a `.reserved` marker is never handed out — that is how `adler-8xtmyt`
    stays the dirty reference room without being deleted, now that its bird code is
    indistinguishable from a fresh one."""
    rooms = tmp_path / "demo-rooms"
    for code in ("adler-8xtmyt", "kranich-b2c3d4", "kiebitz-a1b2c3"):
        (rooms / code).mkdir(parents=True)
    (rooms / "adler-8xtmyt" / ".reserved").write_text("dirty reference room\n",
                                                      encoding="utf-8")
    (rooms.parent / "embed-base").write_text("https://agent.ai4bcm.org/demo/live-x/\n",
                                             encoding="utf-8")
    monkeypatch.setattr(server.graph_files, "ROOMS_DIR", rooms)
    seen = []
    for _ in range(2):
        mcp_http.cookies.clear()          # a fresh device each time, not a re-scan
        r = mcp_http.get("/demo/claim", follow_redirects=False)
        assert r.status_code == 302, r.status_code
        seen.append(r.headers["location"].split("?room=")[1])
    assert "adler-8xtmyt" not in seen, "a reserved room was handed to a tester"
    assert sorted(seen) == ["kiebitz-a1b2c3", "kranich-b2c3d4"]
    mcp_http.cookies.clear()
    r = mcp_http.get("/demo/claim", follow_redirects=False)
    assert r.status_code == 200 and "All demo rooms are taken" in r.text


def test_claim_lane_nginx_location_beats_the_static_demo_alias():
    """The QR claim URL lives under /demo/, which `location ^~ /demo/` serves as static
    files — the exact-match claim location must exist or every scan 404s off the disk.
    `location =` outranks any prefix match, so placement in the file is free but the
    block itself is load-bearing."""
    from pathlib import Path
    text = (Path(__file__).resolve().parent / "deploy" /
            "nginx-agent-ai4bcm.conf").read_text(encoding="utf-8")
    assert "location = /demo/claim" in text
    i = text.index("location = /demo/claim")
    assert "proxy_pass http://127.0.0.1:8787/demo/claim;" in text[i:i + 200]


def test_room_files_download_and_the_listing_still_renders():
    """Owner 2026-08-25: a room file is a handout — clicking it saves it instead of opening
    a text tab. Two traps this pin guards: (1) the header must MISS the autoindex listing
    (URI ends '/') or the room's landing page downloads itself — hence the map with an
    empty default, which nginx drops entirely; (2) any add_header in the location stops
    inheritance of the server-level three, so they are repeated there — Referrer-Policy is
    load-bearing for the live-<hash> embed gate."""
    from pathlib import Path
    text = (Path(__file__).resolve().parent / "deploy" /
            "nginx-agent-ai4bcm.conf").read_text(encoding="utf-8")
    assert "map $uri $rooms_content_disposition" in text
    m = text.index("map $uri $rooms_content_disposition")
    assert 'default ""' in text[m:m + 200]                       # listings keep rendering
    assert '~^/demo/rooms/.+[^/]$ "attachment"' in text[m:m + 200]
    block = _rooms_block(text)
    assert "add_header Content-Disposition $rooms_content_disposition always;" in block
    for repeated in ("Strict-Transport-Security", "X-Content-Type-Options",
                     "Referrer-Policy"):
        assert repeated in block, f"{repeated} lost to add_header inheritance"


def test_the_rooms_block_slice_does_not_depend_on_a_later_location_equals():
    """The slice used to terminate on the next `location =`, which happened to be inside the
    changelog block's comment. Retiring /changelog then raised ValueError inside a test about
    room downloads. The block ends at its own closing brace; nothing after it is our business."""
    conf = (
        "server {\n"
        "    location ^~ /demo/rooms/ {\n"
        "        alias /srv/addendum/demo-rooms/;\n"
        "        autoindex on;\n"
        "        location ~ /\\. { return 404; }\n"
        "    }\n"
        "}\n"
    )
    block = _rooms_block(conf)
    assert "autoindex on;" in block
    assert "alias /srv/addendum/demo-rooms/;" in block


def test_the_release_workflow_can_actually_create_a_release():
    """Four things the spec omitted and each of which fails only at release time:
    the tag trigger, the write permission (GITHUB_TOKEN is read-only by default and the
    create step 403s without it), a manual fallback, and a guard so a junk tag cannot
    publish a junk Release."""
    import pathlib
    wf = (pathlib.Path(__file__).resolve().parent / ".github/workflows/release.yml").read_text()
    assert "tags:" in wf and "v*" in wf
    assert "contents: write" in wf
    assert "workflow_dispatch:" in wf
    assert "changelog_top.py" in wf and "--expect" in wf
    # Anchored, digits-only. The case glob this replaced accepted v0.0.0-probe, because * in
    # `v[0-9]*.[0-9]*.[0-9]*` matches "-probe" — the probe run of 2026-08-25 went straight
    # past the guard it was meant to prove.
    assert r"'^v[0-9]+\.[0-9]+\.[0-9]+$'" in wf
