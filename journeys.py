"""Journey engine: load + validate journey definitions, render a stage per surface."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

# File-relative default: the checkout this module lives in IS the app root, so worktree
# test runs read their own design/ instead of the deployed copy (deployed = same dir).
# design/ is the public workflow-design repo, mounted as a git submodule (C1, 2026-08-18).
_ROOT = Path(os.environ.get("BIA_WORKFLOW_ROOT", Path(__file__).resolve().parent))
JOURNEYS_DIR = Path(os.environ.get("BIA_WORKFLOW_JOURNEYS_DIR", _ROOT / "design"))
PERSONAS_FILE = JOURNEYS_DIR / "personas.json"


@dataclass
class Stage:
    id: str
    goal: str
    # Human-facing stage name, shown verbatim on every stage card (the five traditional BIA
    # names KG fixed with Willem on 2026-08-13; the owner loop is "3a"). The id stays the
    # machine key — next_step calls, tests, SharePoint paths never see the name.
    name: str = ""
    copy_paste_prompt: str = ""
    tools_to_use: list[str] = field(default_factory=list)
    connector_guidance: str = ""
    do_not_paste: str = ""
    approval_gate: str = ""
    reviewer_checklist: list[str] = field(default_factory=list)
    expected_output: str = ""
    cites: list[str] = field(default_factory=list)
    questionnaire: list[str] = field(default_factory=list)
    # 2026-08-16 smart next steps: [{when, offer}] — the model picks the entries whose
    # `when` has happened and ends the turn with them as numbered moves (STAGE_PROTOCOL).
    next_moves: list[dict] = field(default_factory=list)
    # P7 I-1: journey-owned artifact contracts — {path, name?, markers, min_bytes} per
    # canonical stage artifact. The server (graph_files + next_step gate) enforces these;
    # the agent's self-declared expect is additional, never the anchor.
    document_contracts: list[dict] = field(default_factory=list)
    # Company documents that must have been read before this stage may be left. Server-side
    # only: render_stage_tool never ships it, so it costs zero payload — the same treatment
    # reviewer_checklist and expected_output already get. _STAGE_FIELDS is derived from the
    # dataclass, so adding it here is what makes the yaml key legal.
    requires_reads: list[str] = field(default_factory=list)
    next: str | None = None


@dataclass
class Journey:
    id: str
    persona: str
    title: str
    when_to_use: str
    stages: list[Stage]
    # The governance classification of THIS journey's work, phrased once. Server-side only —
    # render_stage_tool never ships it, so it costs zero payload, the same treatment
    # requires_reads gets. It exists because `risk_level` is the max risk of the chunks
    # retrieved for a description the MODEL writes, which made the classification a function
    # of phrasing: measured 2026-08-20, `list files` scored high, `BIA preparation` medium and
    # `scoping` low, and the level lands in the header of the document a human signs.
    risk_task: str | None = None

    def first_stage(self) -> Stage:
        return self.stages[0]

    def stage(self, stage_id: str) -> Stage | None:
        return next((s for s in self.stages if s.id == stage_id), None)

    def stage_index(self, stage_id: str) -> int:
        return next((i for i, s in enumerate(self.stages) if s.id == stage_id), -1)


_STAGE_FIELDS = {f.name for f in fields(Stage)}


def _stage_from(raw: dict) -> Stage:
    unknown = set(raw) - _STAGE_FIELDS
    if unknown:
        raise ValueError(f"stage '{raw.get('id')}' has unknown keys: {sorted(unknown)}")
    return Stage(**raw)


_CONTRACT_KEYS = {"path", "name", "markers", "min_bytes"}


def _contract_problem(c) -> str | None:
    if not isinstance(c, dict):
        return "contract must be a mapping"
    unknown = set(c) - _CONTRACT_KEYS
    if unknown:
        return f"unknown contract keys {sorted(unknown)}"
    path = c.get("path")
    if not isinstance(path, str) or not path.startswith("output/") or path == "output/":
        return "path must be a relative path inside output/"
    markers = c.get("markers")
    if (not isinstance(markers, list) or len(markers) > 32
            or not all(isinstance(m, str) and m.strip() for m in markers)):
        return "markers must be a list of up to 32 non-empty strings"
    mb = c.get("min_bytes")
    if not isinstance(mb, int) or isinstance(mb, bool) or mb < 1:
        return "min_bytes must be a positive integer"
    if "name" in c and (not isinstance(c["name"], str) or not c["name"].strip()):
        return "name must be a non-empty string"
    return None


def validate_journey(j: Journey, valid_chunk_ids: set[str] | None) -> None:
    if not j.stages:
        raise ValueError(f"journey '{j.id}' has no stages")
    ids = {s.id for s in j.stages}
    for s in j.stages:
        if s.next is not None and s.next not in ids:
            raise ValueError(f"{j.id}/{s.id}: next '{s.next}' is not a stage in this journey")
        for c in s.document_contracts:
            problem = _contract_problem(c)
            if problem:
                raise ValueError(f"{j.id}/{s.id}: invalid artifact contract: {problem}")
        if valid_chunk_ids is not None:
            for c in s.cites:
                if c not in valid_chunk_ids:
                    raise ValueError(f"{j.id}/{s.id}: cites unknown chunk id '{c}'")


def load_journeys(journeys_dir: Path = JOURNEYS_DIR, valid_chunk_ids: set[str] | None = None) -> dict[str, Journey]:
    out: dict[str, Journey] = {}
    for yf in sorted(journeys_dir.glob("*.yaml")):
        data = yaml.safe_load(yf.read_text(encoding="utf-8"))
        j = Journey(
            id=data["id"], persona=data["persona"], title=data["title"],
            when_to_use=data["when_to_use"].strip(),
            stages=[_stage_from(s) for s in data["stages"]],
            risk_task=(data.get("risk_task") or "").strip() or None,
        )
        validate_journey(j, valid_chunk_ids)
        out[j.id] = j
    return out


def load_personas(path: Path = PERSONAS_FILE) -> dict[str, dict]:
    return {p["id"]: p for p in json.loads(path.read_text(encoding="utf-8"))}


_PERSONAS: dict[str, dict] | None = None


def _personas() -> dict[str, dict]:
    """Memoised persona lookup for the stage payload (2026-08-19, Tasks 3+4). The voice and its
    worked examples are an ENRICHMENT of the payload; the journey is the contract. So a
    personas file that is missing, unreadable or malformed degrades to no voice rather than
    taking a stage down — a stage the user is standing in matters more than the register it is
    delivered in. `addendum_tools._personas_map()` keeps its own memo for the catalog: that one
    is the tool's whole answer and SHOULD fail loudly, so the two are deliberately not shared."""
    global _PERSONAS
    if _PERSONAS is None:
        try:
            _PERSONAS = load_personas(PERSONAS_FILE)
        except (OSError, ValueError, KeyError, TypeError):
            _PERSONAS = {}
    return _PERSONAS


CONDUCT_FILE = Path(__file__).resolve().parent / "design" / "conduct.md"


def load_conduct(path: Path = CONDUCT_FILE) -> str:
    """design/conduct.md -> the one string carried inside every stage payload, so the rule
    lives in the data the client consumes and not only in the server `instructions` blob
    (which autonomous clients like Codex may ignore).

    Headings and HTML comments are the reader's; they never ship. What is left is joined with
    single spaces, so a paragraph is a rule and line wrapping in the file is free.

    Soft-fails to "" on the same discipline as `_personas()`: the conduct text is an ENRICHMENT
    of the payload and the journey is the contract, so a design file that is missing or
    unreadable degrades to no protocol rather than taking down a stage the user is standing in.

    The text sits in the design package rather than here because it is revised far more often
    than this module — three revisions of one sentence shipped on 2026-08-19 alone, each
    costing a deploy. Its method provenance moved with it; what stays here is mechanism.
    Budget: the rendered string sits in all six run-bia payloads, so one character costs six
    against `payload_sum` — the levers when it must grow are dropping `connector_guidance`
    (-1,719) or `approval_gate` (-1,203), paid in the same commit.
    `test_stage_payload_budget_2026_08_19` pins the size;
    `test_conduct_renders_to_exactly_the_protocol_that_shipped` pins the move itself."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    lines = [l for l in text.splitlines() if not l.lstrip().startswith("#")]
    paragraphs = [" ".join(p.split()) for p in "\n".join(lines).split("\n\n")]
    return " ".join(p for p in paragraphs if p)


STAGE_PROTOCOL = load_conduct()


def _stage_label(name):
    """'Stage 3a · Missing-owner loop' -> '3a'; None when the name carries no label."""
    m = re.match(r"\s*stage\s+(\d+a?)\b", str(name or ""), re.IGNORECASE)
    return m.group(1).lower() if m else None


def _card_text(j: Journey, s: Stage, stage_num: int, total: int) -> str:
    """The stage-card banner, e.g. 'Stage 1 of 5 · Identification of scope'. The denominator
    counts only the stages whose OWN label is a plain integer (`_stage_label` + `.isdigit()`)
    — for run-bia that is five: 3a is excluded by derivation (its label is '3a', not a digit),
    never by a hand-typed 5. A stage whose own label is not a plain integer (3a) keeps its
    name unchanged — bare, no 'of'. A journey whose stage names carry no label at all
    (draft-plan) falls back to the stage's own name, or a plain position fraction when even
    that is empty — the fallback never crashes on a label-free journey.

    2026-08-19: reverses the 2026-08-16 ban on any stage payload carrying an n/total field.
    The ban's reason held — a fraction over all six run-bia *entries* contradicts the deck's
    five — its remedy didn't: it left no way to say 'Stage 1 of 5' at all. This derives the
    fraction instead of banning it."""
    label = _stage_label(s.name)
    if label is None:
        return s.name or f"Stage {stage_num} of {total}"
    if not label.isdigit():
        return s.name
    denominator = sum(1 for st in j.stages if (_stage_label(st.name) or "").isdigit())
    _, sep, rest = s.name.partition(" · ")
    return f"Stage {label} of {denominator} · {rest}" if sep else f"Stage {label} of {denominator}"


def _card_label(j: Journey, s: Stage, stage_num: int, total: int) -> str:
    """The card as the agent prints it — bold, because the protocol says print it VERBATIM, so
    presentation belongs in the value rather than in another instruction nobody has to obey.

    Hans's ruling 2026-08-19, asked as the manager who reads these: "one bold line is a header
    — it says where i am, thats a fact not an instruction. Next: was machinery because it
    labelled my move for me." Measured the same evening: the card rendered as plain body text
    in Teams, the same weight as the sentence under it, and 0 of 19 turns in the graded relay
    run contained any bold at all.

    His limit came with it and lives in the persona voice: bold NOTHING else. "he doesnt stop
    at one: bold the stage, then the process name, then the RTO, and its a generated document
    again." Reverting is this one wrapper, not the derivation below it."""
    return f"**{_card_text(j, s, stage_num, total)}**"


def _tools_to_use(s: Stage) -> list[str]:
    """The stage's tool list, with the reads it will be REFUSED for named first.

    `requires_reads` is server-side and costs no payload, which is why the model was never told
    about it — and so it drafted first and met the gate as a refusal. Measured 2026-08-20 across
    three live runs; the Logistics one turned one missing pair into two refusals, two narrated
    approval turns and two save previews. Derived from `requires_reads` rather than written out
    in the yaml, so the list the model reads cannot drift from the list the server enforces.

    Costs +111 chars, all on stage 1, which is not the largest stage — payload_max is untouched.
    """
    return [f"read_company_file({p})" for p in s.requires_reads] + list(s.tools_to_use)


def render_stage_tool(j: Journey, s: Stage, stage_num: int, total: int) -> dict:
    payload = {
        "journey_id": j.id, "title": j.title, "persona": j.persona,
        "stage_id": s.id, "name": s.name, "card": _card_label(j, s, stage_num, total), "goal": s.goal,
        "protocol": STAGE_PROTOCOL,
        "copy_paste_prompt": s.copy_paste_prompt, "tools_to_use": _tools_to_use(s),
        "questionnaire": s.questionnaire, "next_moves": s.next_moves,
        "connector_guidance": s.connector_guidance,
        "do_not_paste": s.do_not_paste, "approval_gate": s.approval_gate,
        # reviewer_checklist + expected_output stay off the stage TOOL payload (2026-08-19
        # payload budget, Task 6b). Until 2026-08-24 they also rendered into the MCP prompt
        # surface — which no client ever read, so they have reached no model for as long as
        # they have existed here. The fields stay in the yaml, where they are the author's
        # and the reviewer's checklist, readable straight off the Stage object.
        "cites": s.cites, "next": s.next,
        # Literal advance call: Copilot's orchestrator slot-fills next_step's stage_id from
        # visible context and stalled on "Please provide the stage_id" (live 2026-07-21) when
        # the id only existed in a prior turn's tool result. Same pattern as the stage-4
        # reality-loop prompt, which never stalled.
        "advance": f"On approval, call next_step('{j.id}', '{s.id}'{_bia_arg(j)}) — fill "
                   "the ids yourself; never ask the user for them.",
    }
    if s.document_contracts:
        # Single source: cards render required headings FROM the contract, no prose drift.
        payload["document_contracts"] = s.document_contracts
    # LAST in the payload on purpose (2026-08-19, Tasks 3+4): models weight the end, and the
    # register plus four worked turns are what the protocol's rules are FOR. Rules say what to
    # do; a worked turn shows it. Both keys are optional — a persona without them (plan-reviewer)
    # simply does not get them, and neither does a stage whose personas file failed to load.
    persona = _personas().get(j.persona, {})
    for key in ("voice", "examples"):
        if persona.get(key):
            payload[key] = persona[key]
    return payload


def keep_voice_last(payload: dict) -> dict:
    """Re-append the persona keys after a CALLER has added its own — start_journey's `overview`
    and `total_stages`, resume's `resumed`. render_stage_tool puts voice and examples last on
    purpose (models weight the end of a payload); a caller appending after them silently undoes
    that, and the payload the model actually receives is the caller's, not this module's."""
    for key in ("voice", "examples"):
        if key in payload:
            payload[key] = payload.pop(key)
    return payload


def _bia_arg(j: Journey) -> str:
    """', bia=\'<bia>\'' when any stage saves into a per-BIA folder (owner ruling 2026-08-18)."""
    uses = any("<bia>" in c["path"] for st in j.stages for c in st.document_contracts)
    return ", bia='<bia>'" if uses else ""


# render_stage_prompt (the second stage renderer) went with the MCP prompt surface on
# 2026-08-24 — its only production caller was the `run_bia` prompt registration, which no
# client on record ever fetched. render_stage_tool is the one renderer.
