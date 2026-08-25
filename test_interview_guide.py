"""Unit tests for interview_guide — the stage-1 guide content control and its printable
rendering. Pure functions throughout; register/method fixtures copy the REAL archived
marschkamp shapes (flat dict keyed by asset id, en-dash horizons, vertrieb activities) —
the loop that caught the B.9 digest sizing bug."""
import json

import pytest

import interview_guide as ig

HORIZONS = ["0–4 h", "8 h", "24 h", "48 h", "72 h", "1 week"]
METHOD = json.dumps({
    "version": "2026.1-MK",
    "scenarios": [{"id": "financial", "name": "Financial"}],
    "time_horizons": HORIZONS,
    "intolerability_threshold": 4,
})
# The real vertrieb slice: activity wording and consuming asset ids verbatim from the
# archived register (99-Archive export pack, 2026-07-20).
REGISTER = json.dumps({
    "IT-ERP-01": {"name": "SAP S/4HANA", "criticality": 1,
                  "consumers": [{"dept": "vertrieb",
                                 "activity": "order/EDI with grocery-retail customers"}]},
    "LF-SPED-01": {"name": "Refrigerated transport", "criticality": 2,
                   "consumers": [{"dept": "vertrieb",
                                  "activity": "delivery reliability (OTIF) to grocery retail"}]},
    "AN-ZERLEG-01": {"name": "Cutting line", "criticality": 1,
                     "consumers": [{"dept": "vertrieb",
                                    "activity": "order/EDI with grocery-retail customers"}]},
})


def _block(activity, ids, horizons=HORIZONS, questions_extra=0):
    lines = [f"### {activity}",
             f"1. If {activity} stops — whatever the cause — what breaks at "
             + ", ".join(horizons) + "?",
             "2. At which point does the impact become intolerable, and in which categories?",
             "3. How does timing change it — nights, weekends, the peak weeks?",
             "4. What does the activity need per shift — people, rooms, kit?",
             f"5. This activity depends on {', '.join(ids)} — what happens when each is away?",
             "6. Which single points of failure exist? (Recorded for the risk assessment, "
             "not scored here.)"]
    lines += [f"{7 + i}. Anything else that matters here?" for i in range(questions_extra)]
    return "\n".join(lines)


def _doc(guide_body):
    return ("## Scope\nSales department, activities from the register.\n"
            "## Risk and environment\nApproved environment.\n"
            "## Method parameters\nHorizons: " + ", ".join(HORIZONS) + "; threshold 4.\n"
            "## Interview guide\n" + guide_body + "\n")


GOOD_GUIDE = "\n".join([
    _block("order/EDI with grocery-retail customers", ["IT-ERP-01", "AN-ZERLEG-01"]),
    _block("delivery reliability (OTIF) to grocery retail", ["LF-SPED-01"]),
    "### Short version (20 minutes)",
    "1. What breaks first, and when? 2. What can you not tolerate? 3. What do you depend on?",
    "### Bring to the interview",
    "- last year's BIA for sales\n- the current delivery SLA with grocery retail",
])


def test_conforming_guide_has_no_problems():
    assert ig.problems(_doc(GOOD_GUIDE), METHOD, REGISTER) == []


def test_missing_horizon_is_named():
    # '1 week' is the deliberately non-colliding horizon ('8 h' hides inside '48 h' — that
    # substring mask is a documented ponytail ceiling in the module, not tested here).
    short = HORIZONS[:-1]
    guide = "\n".join([
        _block("order/EDI with grocery-retail customers", ["IT-ERP-01", "AN-ZERLEG-01"],
               horizons=short),
        _block("delivery reliability (OTIF) to grocery retail", ["LF-SPED-01"],
               horizons=short),
        "### Short version (20 minutes)", "1.? 2.? 3.?",
        "### Bring to the interview", "- last year's BIA",
    ])
    out = ig.problems(_doc(guide), METHOD, REGISTER)
    assert any("1 week" in p for p in out), out


def test_missing_register_id_is_named_and_present_ids_are_not():
    guide = GOOD_GUIDE.replace("IT-ERP-01", "the ERP")
    out = ig.problems(_doc(guide), METHOD, REGISTER)
    blob = " ".join(out)
    assert "IT-ERP-01" in blob
    assert "AN-ZERLEG-01" not in blob and "LF-SPED-01" not in blob


def test_zero_question_marks_in_a_recognized_block_is_refused():
    flat = _block("delivery reliability (OTIF) to grocery retail",
                  ["LF-SPED-01"]).replace("?", ".")
    guide = "\n".join([
        _block("order/EDI with grocery-retail customers", ["IT-ERP-01", "AN-ZERLEG-01"]),
        flat,
        "### Short version (20 minutes)", "1.? 2.? 3.?",
        "### Bring to the interview", "- last year's BIA",
    ])
    out = ig.problems(_doc(guide), METHOD, REGISTER)
    assert any("delivery reliability" in p and "question" in p for p in out), out


def test_a_guide_with_no_recognized_activity_heading_is_refused():
    guide = "\n".join([
        _block("something entirely invented", ["IT-ERP-01"]),
        "### Short version (20 minutes)", "1.? 2.? 3.?",
        "### Bring to the interview", "- a document",
    ])
    out = ig.problems(_doc(guide), METHOD, REGISTER)
    assert any("register" in p and "activity" in p for p in out), out


def test_the_structural_refusal_teaches_activities_ids_and_horizons():
    """Live 2026-08-24 (calls-2026-W35 06:43:19 → 06:43:55): a guide with no register-headed
    blocks fails the structural check, which MASKS the per-block id/horizon checks — so the
    agent needed a second refusal to learn the asset ids, one structurally guaranteed wasted
    press per run. The structural refusal must carry everything a compliant redraft needs:
    each register activity in its exact wording, its dept, its asset ids, and the method
    horizons verbatim — the batched-refusal rule applied across check tiers, not just within
    one."""
    guide = "\n".join([
        "Impact and dependency questions will be asked per activity.",
        "### Short version (20 minutes)", "1.? 2.? 3.?",
        "### Bring to the interview", "- a document",
    ])
    out = ig.problems(_doc(guide), METHOD, REGISTER)
    structural = [p for p in out if "register" in p and "activity" in p]
    assert structural, out
    blob = " ".join(structural)
    for needle in ("order/EDI with grocery-retail customers", "IT-ERP-01", "AN-ZERLEG-01",
                   "delivery reliability (OTIF) to grocery retail", "LF-SPED-01",
                   "vertrieb", "0–4 h", "1 week"):
        assert needle in blob, (needle, blob)


def test_the_structural_refusal_degrades_without_method_or_register_extras():
    """The enrichment is a courtesy on top of the refusal, never a new failure mode: with no
    method text the listing still names activities and ids, and the refusal still fires."""
    guide = "\n".join([
        "### Short version (20 minutes)", "1.? 2.? 3.?",
        "### Bring to the interview", "- a document",
    ])
    out = ig.problems(_doc(guide), None, REGISTER)
    structural = [p for p in out if "register" in p and "activity" in p]
    assert structural, out
    assert "IT-ERP-01" in " ".join(structural)


@pytest.mark.parametrize("mutation", [
    lambda g: g.replace("### Short version (20 minutes)\n1. What breaks first, and when? "
                        "2. What can you not tolerate? 3. What do you depend on?\n", ""),
    lambda g: g.replace("1. What breaks first, and when? 2. What can you not tolerate? "
                        "3. What do you depend on?", "The short set is available on request."),
])
def test_short_version_missing_or_question_less_is_refused(mutation):
    out = ig.problems(_doc(mutation(GOOD_GUIDE)), METHOD, REGISTER)
    assert any("Short version" in p for p in out), out


def test_bring_list_without_items_is_refused():
    guide = GOOD_GUIDE.replace(
        "- last year's BIA for sales\n- the current delivery SLA with grocery retail",
        "Relevant documentation as appropriate.")
    out = ig.problems(_doc(guide), METHOD, REGISTER)
    assert any("Bring" in p for p in out), out


def test_bring_list_missing_block_names_candidate_documents():
    """Teach-in-the-refusal (proven 2026-08-24 for the structural check): the model that
    drafted an empty bring-list lacked names, not intent. Live 2026-08-24 19:23:18/59
    (calls-2026-W35): this refusal was batched with the activity-heading refusal, then the
    redraft still left the block empty and ate a second refusal. Name real room paths
    (ms-agent-install.md's fixed folder layout) so one refusal is enough."""
    guide = "\n".join([
        _block("order/EDI with grocery-retail customers", ["IT-ERP-01", "AN-ZERLEG-01"]),
        _block("delivery reliability (OTIF) to grocery retail", ["LF-SPED-01"]),
        "### Short version (20 minutes)", "1.? 2.? 3.?",
    ])
    out = ig.problems(_doc(guide), METHOD, REGISTER)
    bring = [p for p in out if "Bring" in p]
    assert bring, out
    blob = " ".join(bring)
    for needle in ("08_Prior-Cycle", "supplier-sla", "impact-criteria"):
        assert needle in blob, (needle, blob)


def test_bring_list_without_items_names_candidate_documents():
    guide = GOOD_GUIDE.replace(
        "- last year's BIA for sales\n- the current delivery SLA with grocery retail",
        "Relevant documentation as appropriate.")
    out = ig.problems(_doc(guide), METHOD, REGISTER)
    bring = [p for p in out if "Bring" in p]
    assert bring, out
    blob = " ".join(bring)
    for needle in ("08_Prior-Cycle", "supplier-sla", "impact-criteria"):
        assert needle in blob, (needle, blob)


def test_bring_list_candidates_do_not_depend_on_method_or_register():
    """The candidates are fixed room paths (ms-agent-install.md Part A), not data read from
    method.json or the register — so they still teach when a company lacks either source,
    the same degrade rule every other enrichment in this module follows."""
    guide = "\n".join(["### Short version (20 minutes)", "1.? 2.? 3.?"])
    out = ig.problems(_doc(guide), None, None)
    bring = [p for p in out if "Bring" in p]
    assert bring, out
    assert "08_Prior-Cycle" in " ".join(bring)


def test_missing_method_or_register_skips_those_checks_only():
    """The pure twin of the no-register-still-saves pin: a company without the sources can
    still save stage 1; only the universal checks (questions, short set, bring list) run."""
    assert ig.problems(_doc(GOOD_GUIDE), None, None) == []
    hollow = _doc("A guide will be prepared.")
    out = ig.problems(hollow, None, None)
    assert out, "U1-U3 must still catch a hollow guide with no sources"


def test_horizons_in_method_parameters_section_do_not_satisfy_the_guide_check():
    """Section scoping is what makes the check mean anything: the horizons legitimately
    appear under ## Method parameters in every conforming document."""
    no_horizon_questions = "\n".join([
        _block("order/EDI with grocery-retail customers", ["IT-ERP-01", "AN-ZERLEG-01"],
               horizons=["over time"]),
        _block("delivery reliability (OTIF) to grocery retail", ["LF-SPED-01"],
               horizons=["over time"]),
        "### Short version (20 minutes)", "1.? 2.? 3.?",
        "### Bring to the interview", "- last year's BIA",
    ])
    out = ig.problems(_doc(no_horizon_questions), METHOD, REGISTER)
    assert any("0–4 h" in p or "1 week" in p for p in out), out


def test_a_good_thirteen_question_guide_is_not_refused():
    """Pins the tier decision (owner, 2026-08-24): the 6-12 band is writing guidance, never
    enforced — a genuinely thorough guide must not be refused for thoroughness. Do not add a
    ceiling here without a fresh owner ruling."""
    guide = "\n".join([
        _block("order/EDI with grocery-retail customers", ["IT-ERP-01", "AN-ZERLEG-01"],
               questions_extra=7),   # 13 questions in this block
        _block("delivery reliability (OTIF) to grocery retail", ["LF-SPED-01"]),
        "### Short version (20 minutes)", "1.? 2.? 3.?",
        "### Bring to the interview", "- last year's BIA",
    ])
    assert ig.problems(_doc(guide), METHOD, REGISTER) == []


def test_render_contains_only_the_guide_section():
    html = ig.render(_doc(GOOD_GUIDE), "marschkamp", "sales")
    assert "order/EDI with grocery-retail customers" in html
    assert "Approved environment" not in html          # ## Risk and environment stays out
    assert "Sales department, activities" not in html  # ## Scope stays out
    assert "size: A4" in html or "size:A4" in html
    assert "break-inside" in html
    import brand
    assert brand.TOKENS in html                        # composed via STYLE, not pasted


def test_render_escapes_model_content():
    doc = _doc(GOOD_GUIDE.replace(
        "### order/EDI with grocery-retail customers",
        "### order/EDI <script>alert(1)</script> customers"))
    html = ig.render(doc, "marschkamp", "sales")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_publish_writes_under_dep_graph_public_and_returns_the_url(tmp_path, monkeypatch):
    import dep_graph
    monkeypatch.setattr(dep_graph, "PUBLIC", tmp_path / "graph-pages")
    url = ig.publish("marschkamp", "sales", _doc(GOOD_GUIDE).encode("utf-8"))
    out = tmp_path / "graph-pages" / "marschkamp" / "sales" / "guide.html"
    assert out.exists()
    assert url == "https://agent.ai4bcm.org/demo/graph/marschkamp/sales/guide.html"
    assert "order/EDI" in out.read_text(encoding="utf-8")


# ── Hans's page ruling, 2026-08-24 (#bia-workflow) — each rendered element pinned ────────


def test_short_version_renders_before_the_activity_blocks():
    """'the short version goes on page one, under the title line, before the activity
    blocks. i walk in, he says i have twenty minutes.'"""
    html = ig.render(_doc(GOOD_GUIDE), "marschkamp", "sales")
    assert html.index("Short version") < html.index("order/EDI with grocery-retail customers")


def test_activity_block_carries_owner_and_deputy_from_the_register():
    """'nobody is on the page. print the activity owner and his deputy by name, from the
    register, not a blank.' Register assets carry owner_name and stellvertreter."""
    reg = json.loads(REGISTER)
    reg["IT-ERP-01"]["owner_name"] = "Marek Sobotta"
    reg["IT-ERP-01"]["stellvertreter"] = "Ines Dreyer"
    html = ig.render(_doc(GOOD_GUIDE), "marschkamp", "sales",
                     register_text=json.dumps(reg))
    assert "Marek Sobotta" in html and "Ines Dreyer" in html


def test_impact_question_carries_the_empty_horizons_grid():
    """'under the impact-over-time question print the empty grid - six columns headed
    0-4h ... one week - so the numbers go straight in where they belong.'"""
    html = ig.render(_doc(GOOD_GUIDE), "marschkamp", "sales", method_text=METHOD)
    for h in HORIZONS:
        assert f"<th>{h}</th>" in html, h


def test_questions_carry_writing_lines():
    """'nowhere to write ... four blank lines under every question.'"""
    html = ig.render(_doc(GOOD_GUIDE), "marschkamp", "sales")
    assert html.count('class="wline"') >= 4


def test_each_activity_block_ends_with_the_confirm_line():
    """'seen and corrected by ______ date ______ - that is the line that turns my notes
    into the owner's evidence.' (His Q4 ruling, materialized on paper.)"""
    html = ig.render(_doc(GOOD_GUIDE), "marschkamp", "sales")
    assert html.count("Seen and corrected by") == 2      # one per activity block


def test_interview_record_blanks_and_page_numbers_present():
    """'blanks i fill in: date, start and end time, who else was in the room, and who ran
    it' + 'page numbers: put them back. page 3 of 7.'"""
    html = ig.render(_doc(GOOD_GUIDE), "marschkamp", "sales")
    assert "Date" in html and "Start" in html and "In the room" in html
    assert "counter(page)" in html and "counter(pages)" in html


def test_meta_line_ties_the_paper_to_a_file_version():
    """'the small line under the title should carry the version of the saved file, not
    just the date' — byte size + date is the version tie the server can print."""
    doc = _doc(GOOD_GUIDE)
    html = ig.render(doc, "marschkamp", "sales")
    assert f"{len(doc.encode('utf-8')):,} bytes" in html


def test_counts_reports_questions_and_activities():
    """The receipt's 'N questions across M activities' — 'the counts are the thing that
    tells me the guide is real without opening it.'"""
    q, a = ig.counts(_doc(GOOD_GUIDE))
    assert a == 2
    assert q >= 12   # six per activity in the fixture; short-set questions excluded
