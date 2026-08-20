#!/usr/bin/env python3
"""Regenerate the synthetic fixture corpus in tests/fixtures/.

The suite cannot run without a chunk corpus: server.py builds its index at module scope and
validates every journey's `cites:` against it, so an absent corpus aborts pytest collection
outright. The real corpus is built from the BCI AI Addendum, which is not this project's to
relicense and is therefore not in the public repository (see README). This script produces a
stand-in.

Nothing here may be copied from addendum-clean.md or knowledge/. Every sentence below is
invented for the fixture, and `source_file` is "fixture" on every chunk so a fixture chunk is
never mistaken for real content.

The required id set is DERIVED from design/*.yaml rather than hardcoded, so a journey that
starts citing a new section fails here -- loudly, at regeneration time -- instead of turning
into a mystery collection error later.

Usage:  python scripts/make_fixture_chunks.py --out tests/fixtures
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESIGN_DIR = ROOT / "design"

# Ids the test suite names literally rather than reaching through a journey. Kept separate from
# the derived set because nothing in design/ requires them:
#   pp1-introduction, pp6-introduction  test_smoke.test_frontmatter_does_not_leak_into_chunks
#   faq-how-is-chatgpt-...              test_smoke.test_mode_metadata_preserved_and_filterable
LITERAL_TEST_IDS = [
    "pp1-introduction",
    "pp6-introduction",
    "faq-how-is-chatgpt-or-claude-chat-different-from-claude-code-codex-or-connected-automation",
]

# Vocabulary the fixture places deliberately, because a test reads the consequence:
#   "BIA" / "business impact analysis"       test_search_and_fetch, test_search_hints_guided_journey
#   exercise / scenario / tabletop / design  ONLY in medium-risk pp6-introduction, because
#                                            identify_ai_risks takes the MAX risk of the top-4 hits
#                                            and test_identify_ai_risks_exercise_is_low_risk wants
#                                            low or medium out of that phrasing
#   "connected automation" / "approval gates"  chunks whose mode carries operational_workflow
#   intellectual property / licensing / copyright  nowhere, so that query retrieves nothing and
#                                            test_search_no_hint_for_non_journey_topic sees no hint

CHUNKS: dict[str, dict] = {
    "pp1-introduction": dict(
        pp="pp1", section_type="introduction", title="Introduction",
        bcm_process="bcms-governance", risk_level="medium", confidentiality="internal",
        intended_user="practitioner,manager", output_type="guidance", mode="chat_guidance",
        related_controls=["human-review", "document-control"], related_examples=[],
        text="This fixture stands in for the opening section. An assistant may support the "
             "management system but never owns a decision inside it. Every output carries an "
             "author, a date and a named reviewer, and the record states which release of the "
             "guidance was in force when the work was done.",
    ),
    "pp3-process": dict(
        pp="pp3", section_type="process", title="Process",
        bcm_process="bia,risk-assessment", risk_level="high", confidentiality="high",
        intended_user="practitioner", output_type="workflow", mode="operator_integrated",
        related_controls=["approved-tools", "owner-validation", "data-classification"],
        related_examples=["demo-bia"],
        text="A BIA runs in ordered stages, and a stage that has not been reviewed does not open "
             "the next one. A business impact analysis begins by fixing scope, then collects "
             "dependency and timing evidence department by department, then reconciles the "
             "answers across departments before any MTPD is agreed. The assistant drafts; the "
             "process owner decides.",
    ),
    "pp3-methods": dict(
        pp="pp3", section_type="methods", title="Methods and Techniques",
        bcm_process="bia,risk-assessment", risk_level="high", confidentiality="high",
        intended_user="practitioner", output_type="techniques", mode="operator_integrated",
        related_controls=["approved-tools", "owner-validation", "data-classification"],
        related_examples=["demo-bia"],
        text="Interview, workshop and questionnaire all remain valid ways to gather BIA evidence, "
             "and the assistant replaces none of them. It prepares the material, keeps wording "
             "consistent between departments, and flags where two answers cannot both be true. A "
             "business impact analysis that moves an MTPD without naming the evidence is not "
             "finished.",
    ),
    "pp3-outcomes": dict(
        pp="pp3", section_type="outcomes", title="Outcomes and Review",
        bcm_process="bia,risk-assessment", risk_level="high", confidentiality="high",
        intended_user="practitioner,manager", output_type="review", mode="operator_integrated",
        related_controls=["owner-validation", "management-review", "audit-trail"],
        related_examples=[],
        text="The output of a BIA is a reviewed record, not a transcript. Each department's MTPD "
             "and recovery priority is written down beside the evidence that produced it and the "
             "name of the person who agreed it. A management review closes the round; an "
             "unreviewed draft carries no authority.",
    ),
    "pp3-typical_uses": dict(
        pp="pp3", section_type="typical_uses", title="Typical AI uses",
        bcm_process="bia,risk-assessment", risk_level="medium", confidentiality="medium",
        intended_user="practitioner", output_type="guidance",
        mode="chat_guidance,operator_integrated",
        related_controls=["approved-tools", "data-classification", "owner-validation"],
        related_examples=[],
        text="Typical assistant work in a BIA is preparation and consistency: drafting the "
             "interview guide, restating an answer in the department's own vocabulary, and "
             "comparing this round against the last. It does not set an MTPD and it does not "
             "rank a recovery priority.",
    ),
    "pp3-minimum_controls": dict(
        pp="pp3", section_type="minimum_controls", title="Minimum controls",
        bcm_process="bia,risk-assessment", risk_level="high", confidentiality="high",
        intended_user="practitioner,manager", output_type="governance", mode="operator_integrated",
        related_controls=["approved-tools", "data-classification", "owner-validation",
                          "change-control"],
        related_examples=[],
        text="Minimum controls for BIA work: an approved tool, a named owner for every figure, a "
             "data classification agreed before any transcript is uploaded, and a change record "
             "whenever an already agreed MTPD moves.",
    ),
    "pp4-process": dict(
        pp="pp4", section_type="process", title="Process",
        bcm_process="solutions-design", risk_level="high", confidentiality="high",
        intended_user="practitioner,manager", output_type="workflow", mode="operator_integrated",
        related_controls=["approved-sources", "stakeholder-review", "finance-review"],
        related_examples=[],
        text="Continuity option selection starts from the recovery time the analysis already "
             "agreed and asks what capability closes the gap. Options are costed, compared "
             "against that objective, and put to the budget holder. The assistant assembles the "
             "comparison; it does not choose.",
    ),
    "pp4-methods": dict(
        pp="pp4", section_type="methods", title="Methods and Techniques",
        bcm_process="solutions-design", risk_level="high", confidentiality="high",
        intended_user="practitioner,manager", output_type="techniques", mode="operator_integrated",
        related_controls=["approved-sources", "stakeholder-review", "human-review"],
        related_examples=[],
        text="Option comparison, cost modelling and supplier assessment are the usual techniques. "
             "The assistant keeps the comparison honest: the same criteria applied to every "
             "option, and a stated source behind every number.",
    ),
    "pp4-minimum_controls": dict(
        pp="pp4", section_type="minimum_controls", title="Minimum controls",
        bcm_process="solutions-design", risk_level="high", confidentiality="high",
        intended_user="practitioner,manager", output_type="governance", mode="operator_integrated",
        related_controls=["stakeholder-review", "finance-review", "approved-tools"],
        related_examples=[],
        text="No option is adopted without stakeholder review, a finance review of the recurring "
             "cost, and written confirmation that the capability meets the recovery objective it "
             "was picked for.",
    ),
    "pp4-typical_uses": dict(
        pp="pp4", section_type="typical_uses", title="Typical AI uses",
        bcm_process="solutions-design", risk_level="medium", confidentiality="high",
        intended_user="practitioner,manager", output_type="guidance",
        mode="chat_guidance,operator_integrated",
        related_controls=["human-review", "approved-tools"], related_examples=[],
        text="Typical uses are drafting option summaries and reshaping a supplier response into "
             "the same form as the others, so a manager can read them side by side.",
    ),
    "pp5-process": dict(
        pp="pp5", section_type="process", title="Process",
        bcm_process="plan-management", risk_level="high", confidentiality="high",
        intended_user="practitioner", output_type="workflow",
        mode="operator_integrated,operational_workflow",
        related_controls=["owner-validation", "change-control", "approved-sources"],
        related_examples=[],
        text="Plan authoring runs from an agreed template. The assistant may draft a section and "
             "propose an update, but a plan changes only through change control, with the plan "
             "owner's approval recorded against the version it applies to.",
    ),
    "pp5-methods": dict(
        pp="pp5", section_type="methods", title="Methods and Techniques",
        bcm_process="plan-management", risk_level="high", confidentiality="high",
        intended_user="practitioner", output_type="techniques",
        mode="operator_integrated,operational_workflow",
        related_controls=["owner-validation", "change-control", "approval-gates"],
        related_examples=[],
        text="Structured authoring, controlled update and version comparison. Connected "
             "automation may propose a change to a plan; approval gates decide whether it lands, "
             "and the audit trail records who let it through.",
    ),
    "pp5-minimum_controls": dict(
        pp="pp5", section_type="minimum_controls", title="Minimum controls",
        bcm_process="plan-management", risk_level="high", confidentiality="high",
        intended_user="practitioner,manager", output_type="governance",
        mode="operator_integrated,operational_workflow",
        related_controls=["owner-validation", "change-control", "approval-gates", "fallback"],
        related_examples=[],
        text="Owner validation before publication, change control on every edit, approval gates "
             "on any automated update, and a fallback copy that does not depend on the system "
             "being changed.",
    ),
    "pp5-outcomes": dict(
        pp="pp5", section_type="outcomes", title="Outcomes and Review",
        bcm_process="plan-management", risk_level="high", confidentiality="high",
        intended_user="practitioner,manager", output_type="review",
        mode="operator_integrated,operational_workflow",
        related_controls=["owner-validation", "change-control", "fallback", "exercise-testing"],
        related_examples=[],
        text="The outcome is a current plan with a known review date and an audit trail showing "
             "who changed what and when. Validation findings feed back here rather than into a "
             "separate document nobody opens.",
    ),
    "pp5-typical_uses": dict(
        pp="pp5", section_type="typical_uses", title="Typical AI uses",
        bcm_process="plan-management", risk_level="medium", confidentiality="high",
        intended_user="practitioner", output_type="guidance",
        mode="chat_guidance,operator_integrated",
        related_controls=["owner-validation", "approved-tools"], related_examples=[],
        text="Typical uses are drafting a section from agreed inputs and comparing a plan against "
             "its previous version so a reviewer can see exactly what moved.",
    ),
    # The only home for exercise vocabulary. See the note above CHUNKS: identify_ai_risks takes
    # the max risk of the top-4 hits, so these words must not reach a high-risk chunk.
    "pp6-introduction": dict(
        pp="pp6", section_type="introduction", title="Introduction",
        bcm_process="exercise,validation", risk_level="medium", confidentiality="medium",
        intended_user="practitioner,manager", output_type="guidance",
        mode="chat_guidance,operator_integrated",
        related_controls=["exercise-director-review", "human-review", "approved-tools"],
        related_examples=[],
        text="Validation asks whether the plan holds when it is used. A tabletop exercise, a "
             "scenario walkthrough and a live test each answer a different question, and "
             "exercise scenario design starts from the risks already named. The assistant may "
             "draft a fictional scenario and an injects list; the exercise director owns the "
             "objectives and the debrief.",
    ),
    "faq-can-ai-update-my-bcp-automatically": dict(
        pp="pp5", section_type="faq", title="Can AI update my BCP automatically?",
        bcm_process="", risk_level="", confidentiality="",
        intended_user="practitioner,manager", output_type="governance",
        mode="operational_workflow",
        related_controls=["owner-validation", "change-control", "approval-gates", "audit-trail"],
        related_examples=[],
        text="Not on its own. An assistant can prepare an update and put it in front of the "
             "owner, but the change lands through change control, with an approval recorded and "
             "an audit trail behind it.",
    ),
    "faq-how-do-i-use-ai-to-review-a-continuity-plan": dict(
        pp="pp5", section_type="faq", title="How do I use AI to review a continuity plan?",
        bcm_process="", risk_level="", confidentiality="",
        intended_user="practitioner", output_type="guidance", mode="operator_integrated",
        related_controls=["owner-validation", "approved-tools"], related_examples=[],
        text="Give it the plan and the criteria you would apply yourself, ask where the plan is "
             "silent, and check every gap it names against the document. Treat the answer as a "
             "reviewer's note, not as a verdict.",
    ),
    "faq-how-is-chatgpt-or-claude-chat-different-from-claude-code-codex-or-connected-automation": dict(
        pp=None, section_type="faq",
        title="How is ChatGPT or Claude chat different from Claude Code, Codex, or connected "
              "automation?",
        bcm_process="", risk_level="", confidentiality="",
        intended_user="practitioner,manager,operator", output_type="guidance",
        mode="chat_guidance,operator_integrated,operational_workflow",
        related_controls=["data-classification", "source-boundaries", "approval-gates",
                          "human-review"],
        related_examples=[],
        text="Chat is a conversation with no reach into your files. An integrated operator reads "
             "and writes inside a workspace you have granted it. Connected automation runs "
             "without a person watching each step, which is why it needs approval gates and an "
             "audit trail before it touches anything that matters.",
    ),
    "prompts-bia-preparation-interview-guide": dict(
        pp="pp3", section_type="prompt", title="BIA Preparation - Interview Guide",
        bcm_process="bia", risk_level="medium", confidentiality="medium",
        intended_user="practitioner", output_type="prompt",
        mode="chat_guidance,operator_integrated",
        related_controls=["approved-tools", "owner-validation"], related_examples=["demo-bia"],
        text="Fixture prompt. Ask for an interview guide covering one department, in that "
             "department's own vocabulary, over dependencies, timing, and the evidence behind "
             "each answer. Require it to end with the question to ask when an answer contradicts "
             "the last round.",
    ),
    "prompts-bia-output-review-consistency-check": dict(
        pp="pp3", section_type="prompt", title="BIA Output Review - Consistency Check",
        bcm_process="bia", risk_level="high", confidentiality="high",
        intended_user="practitioner", output_type="prompt", mode="operator_integrated",
        related_controls=["approved-tools", "owner-validation", "data-classification"],
        related_examples=[],
        text="Fixture prompt. Ask for every place two departments describe one dependency "
             "differently, every MTPD that moved without a stated reason, and every figure with "
             "no named owner. Require a list, not a narrative.",
    ),
    "workflows-bia-method-parameters": dict(
        pp="pp3", section_type="workflow", title="BIA method parameters",
        bcm_process="bia-method", risk_level="medium", confidentiality="high",
        intended_user="practitioner", output_type="reference", mode="",
        related_controls=["owner-validation", "data-classification"],
        related_examples=["demo-bia"],
        text="Fixture parameters for a BIA round: the impact scale in use, the impact categories, "
             "the time bands, and whether MTPD is agreed per department or per process. Recorded "
             "once, so every department is measured the same way.",
    ),
}


def cited_ids(design_dir: pathlib.Path) -> set[str]:
    """Every chunk id cited by any journey. journeys.py globs design/*.yaml, so this must too --
    reading only run-bia.yaml misses draft-plan.yaml, whose cites are validated just as hard."""
    found: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "cites":
                    found.update(value if isinstance(value, list) else [value])
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for path in sorted(design_dir.glob("*.yaml")):
        walk(yaml.safe_load(path.read_text(encoding="utf-8")))
    return found


def build(design_dir: pathlib.Path) -> tuple[list[dict], dict]:
    required = cited_ids(design_dir) | set(LITERAL_TEST_IDS)
    missing = sorted(required - set(CHUNKS))
    if missing:
        raise SystemExit(
            "The journeys cite chunk ids this fixture does not define:\n  "
            + "\n  ".join(missing)
            + "\n\nAdd them to CHUNKS in this script (invented text only -- nothing may be copied "
              "from the real corpus), then re-run."
        )
    unused = sorted(set(CHUNKS) - required)
    if unused:
        print(f"note: {len(unused)} fixture chunk(s) cited by no journey: {', '.join(unused)}",
              file=sys.stderr)

    chunks = []
    for cid in sorted(CHUNKS):
        spec = CHUNKS[cid]
        text = " ".join(spec["text"].split())
        chunks.append({
            "id": cid,
            "pp": spec["pp"],
            "section_type": spec["section_type"],
            "title": spec["title"],
            "breadcrumb": spec["title"],
            "text": text,
            "url": f"https://example.invalid/kb/{cid}/",
            "char_count": len(text),
            "bcm_process": spec["bcm_process"],
            "ai_capability": "",
            "risk_level": spec["risk_level"],
            "confidentiality": spec["confidentiality"],
            "intended_user": spec["intended_user"],
            "output_type": spec["output_type"],
            "mode": spec["mode"],
            "related_controls": spec["related_controls"],
            "related_examples": spec["related_examples"],
            # Never "addendum-clean.md" or a knowledge/ filename: a fixture chunk has to be
            # identifiable as synthetic from the chunk itself.
            "source_file": "fixture",
        })

    index = {
        "title": "Fixture knowledge index (synthetic, not the AI Addendum)",
        "chunk_count": len(chunks),
        "topics": [
            {"id": c["id"], "title": c["title"], "breadcrumb": c["breadcrumb"],
             "pp": c["pp"], "section_type": c["section_type"], "url": c["url"]}
            for c in chunks
        ],
    }
    return chunks, index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=pathlib.Path, default=ROOT / "tests" / "fixtures",
                        help="directory to write chunks.json and index.json into")
    parser.add_argument("--design", type=pathlib.Path, default=DESIGN_DIR,
                        help="journey directory to read cites from")
    args = parser.parse_args()

    chunks, index = build(args.design)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "chunks.json").write_text(
        json.dumps(chunks, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.out / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(chunks)} chunks to {args.out}")


if __name__ == "__main__":
    main()
