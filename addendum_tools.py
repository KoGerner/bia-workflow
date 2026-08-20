"""Pure-Python tool bodies behind the MCP server."""

from __future__ import annotations
import os
import re
from pathlib import Path

import httpx

from retrieval import AddendumIndex
import graph_files
import journeys as journey_engine

_DATA_DIR = Path(os.environ.get("BIA_WORKFLOW_DATA_DIR", Path(__file__).resolve().parent / "data"))
_index: AddendumIndex | None = None


def _idx() -> AddendumIndex:
    global _index
    if _index is None:
        _index = AddendumIndex(_DATA_DIR)
    return _index


_journeys: dict | None = None


def _journeys_map():
    global _journeys
    if _journeys is None:
        _journeys = journey_engine.load_journeys(valid_chunk_ids={c.id for c in _idx().chunks})
    return _journeys


# bcm_process keyword → guided journey. Lets search/fetch nudge the staged flow when a
# result lands in journey territory (department-reply has no content signal, so it relies
# on the tool descriptions + server instructions instead).
JOURNEY_FOR_PROCESS = {
    "bia": "run-bia",
    "risk-assessment": "run-bia",
}


def _journey_for_process(bcm_process):
    for proc in (bcm_process or "").lower().split(","):
        jid = JOURNEY_FOR_PROCESS.get(proc.strip())
        if jid:
            return jid
    return None


def _journey_hint(jid):
    return (
        f"This task maps to the '{jid}' guided journey — a multi-stage BCM task with mandatory "
        f"human approval gates. Call start_journey('{jid}') and present one stage at a time; do "
        f"not draft the whole artifact in one shot."
    )


def journey_hint_for_results(results):
    """Return a staging hint if any search result sits in journey territory, else None."""
    for r in results:
        jid = _journey_for_process(r.get("bcm_process", ""))
        if jid:
            return _journey_hint(jid)
    return None


def search_fn(query, pp=None, output_type=None, risk_level=None, confidentiality=None, bcm_process=None, mode=None):
    pp_norm = pp.lower().strip() if pp else None
    return [
        {"id": c.id, "title": c.breadcrumb, "url": c.url, "section_type": c.section_type,
         "pp": c.pp or "", "risk_level": c.risk_level, "output_type": c.output_type,
         "mode": c.mode, "bcm_process": c.bcm_process, "confidentiality": c.confidentiality}
        for c in _idx().search(query, pp=pp_norm, output_type=output_type, risk_level=risk_level,
                               confidentiality=confidentiality, bcm_process=bcm_process, mode=mode)
    ]


def fetch_fn(chunk_id):
    c = _idx().get(chunk_id)
    if not c:
        return {"error": "not_found", "message": f"No section found for id {chunk_id}."}
    payload = {
        "id": c.id, "title": c.breadcrumb, "text": c.text, "url": c.url,
        "metadata": {"breadcrumb": c.breadcrumb, "pp": c.pp or "", "section_type": c.section_type,
                     "risk_level": c.risk_level, "output_type": c.output_type,
                     "mode": c.mode, "bcm_process": c.bcm_process,
                     "confidentiality": c.confidentiality,
                     "related_controls": c.related_controls, "source_file": c.source_file},
    }
    jid = _journey_for_process(c.bcm_process)
    if jid:
        payload["guided_journey"] = _journey_hint(jid)
    return payload


def list_topics_fn():
    return _idx().topics()


def get_workflow_fn(workflow_id):
    idx = _idx()
    c = idx.get(workflow_id)
    if not c or c.section_type != "workflow":
        results = idx.search(workflow_id, output_type="workflow", limit=1) or \
                  [ch for ch in idx.chunks if ch.section_type == "workflow"]
        c = results[0] if results else None
    if not c:
        return {"error": "not_found", "message": f"No workflow found for '{workflow_id}'.",
                "available_workflow_ids": [ch.id for ch in idx.chunks if ch.section_type == "workflow"]}
    return {
        "id": c.id, "title": c.breadcrumb, "text": c.text, "url": c.url,
        "metadata": {"pp": c.pp or "", "bcm_process": c.bcm_process, "risk_level": c.risk_level,
                     "related_controls": c.related_controls, "related_examples": c.related_examples},
    }


def start_journey_fn(journey_id):
    js = _journeys_map()
    j = js.get(journey_id)
    if not j:
        wf = get_workflow_fn(journey_id)  # folded: closest single workflow
        if "error" not in wf:
            wf["note"] = "No multi-stage journey matched; returning the closest single workflow."
            return wf
        return {"error": "not_found",
                "message": f"No journey or workflow matched '{journey_id}'.",
                "available_journeys": [
                    {"id": x.id, "title": x.title, "when_to_use": x.when_to_use} for x in js.values()
                ]}
    graph_files.forget_reads()   # a journey is actually starting; it has read nothing yet
    set_risk_task(j.risk_task)   # ... and its governance classification is the journey's, not the model's
    s = j.first_stage()
    payload = journey_engine.render_stage_tool(j, s, 1, len(j.stages))
    payload["overview"] = j.when_to_use
    payload["total_stages"] = len(j.stages)
    return journey_engine.keep_voice_last(payload)


def _fetch_artifact(company, path):
    # Gate seam: conftest replaces this in pytest so tests never read live Graph.
    return graph_files.read_file(company, path)


def _bia_arg_error(stage, stage_num, bia):
    """Per-BIA folders (owner ruling 2026-08-18): a `<bia>` contract can only be proven in the
    folder the agent names — no folder, no read; a non-slug folder is refused with the slug."""
    if not bia:
        return {"error": "stage_incomplete",
                "message": (f"Stage {stage_num} cannot be verified — its documents live in this "
                            "BIA's own folder output/<bia>/ (the process slug named in the Stage 1 "
                            "card). Call next_step again with bia='<slug>', e.g. bia='slaughter'."),
                "next_move": (f"Call next_step('run-bia', '{stage.id}', bia='<slug>') with this "
                              "BIA's folder slug — never ask the user for it")}
    if not graph_files.BIA_SLUG.fullmatch(str(bia)):
        fixed = graph_files.slugify(bia) or "slaughter"
        return {"error": "stage_incomplete",
                "message": (f"bia='{bia}' is not a folder slug — use lowercase-hyphen "
                            f"(e.g. bia='{fixed}', folder output/{fixed}/) and call next_step again."),
                "next_move": f"Call next_step('run-bia', '{stage.id}', bia='{fixed}')"}
    return None


def _advance_gate_error(stage, stage_num, company, bia=None):
    """P7 I-1 part 2: a named completed stage advances only when its canonical artifacts
    are saved and meet the journey-owned contract — closes the referent-substitution
    route (I-1 as fired) and the skipped-artifact class (I-5). Pattern paths ('*') are
    write-time contracts only: the owner side-quest's N/A branch is register-dependent
    and not server-derivable. Fails CLOSED on a data-source outage, legibly.
    `<bia>` paths are read in the folder the agent names (bia=), never guessed."""
    # W4/W7/W12: the method already tells stage 1 to offer options from the company's own
    # material and to suggest what the interviewee did not name. Nothing checked, and the
    # 2026-08-19 run called start_journey then read nothing at all. The instruction was
    # never the problem, so this adds no instruction — it makes the gate ask.
    # Every unread source in ONE rejection, same reason as the write jaw: naming them one at a
    # time makes the round trips scale with the number of required reads, and 2026-08-20 09:32
    # produced two consecutive stage_incomplete rejections for exactly that.
    missing = []
    for path in getattr(stage, "requires_reads", []):
        if path in graph_files.reads_seen(company):
            continue
        try:
            probe = _fetch_artifact(company, path)
        except httpx.HTTPError:
            continue  # data source down: the loop below reports that legibly
        if "error" in probe:
            continue  # a document this company never supplied cannot be demanded
        missing.append(path)
    if missing:
        calls = "; ".join(f"read_company_file(company='{company}', path='{p}')" for p in missing)
        return {"error": "stage_incomplete",
                "message": (f"Stage {stage_num} cannot advance: never read in this journey — "
                            f"{', '.join(missing)}. This stage's proposals must come from the "
                            "company's own material, not from memory."),
                "next_move": f"Call {calls} — all of them — then retry next_step"}
    for c in stage.document_contracts:
        if "*" in c["path"]:
            continue
        name = c.get("name") or stage.id
        path = c["path"]
        if graph_files.BIA_PLACEHOLDER in path:
            guard = _bia_arg_error(stage, stage_num, bia)
            if guard:
                return guard
            path = path.replace(graph_files.BIA_PLACEHOLDER, bia)
        try:
            got = _fetch_artifact(company, path)
        except httpx.HTTPError:
            return {"error": "stage_incomplete",
                    "message": (f"Stage {stage_num} cannot be verified right now — the "
                                "company data source is unreachable. Try again shortly; "
                                "the stage artifact check must pass before the journey "
                                "moves on."),
                    "next_move": ("Retry next_step in a moment; if it persists, tell the "
                                  "user the file check is unavailable")}
        if "error" in got:
            if "not found" not in got["error"]:
                # Unknown company / unreadable folder: a verification failure, never
                # advice to re-save into a folder that cannot be read.
                return {"error": "stage_incomplete",
                        "message": (f"Stage {stage_num} cannot be verified right now — "
                                    "the company folder could not be read. Check the "
                                    "company name and data source, then try again."),
                        "next_move": ("Retry next_step in a moment; if it persists, tell "
                                      "the user the file check is unavailable")}
            return {"error": "stage_incomplete",
                    "message": (f"Stage {stage_num} isn't finished — the {name} "
                                f"({path}) hasn't been saved. Save it with every "
                                "required section, then advance."),
                    "next_move": (f"Write {path} with sections "
                                  f"{', '.join(c['markers'])}, then call next_step again")}
        content = got.get("content", "")
        size = len(content.encode("utf-8"))
        missing = [m for m in c["markers"] if m not in content]
        if size < c["min_bytes"] or missing:
            what = f"is {size} bytes"
            if missing:
                what += " and missing " + ", ".join(repr(m) for m in missing[:6])
            return {"error": "stage_incomplete",
                    "message": (f"Stage {stage_num} isn't finished — the {name} "
                                f"({path}) {what}; the stage artifact needs at "
                                f"least {c['min_bytes']} bytes and every required "
                                "section. Save the full version you presented, then "
                                "advance."),
                    "next_move": (f"Write {path} with sections "
                                  f"{', '.join(c['markers'])}, then call next_step again")}
    return None


def next_step_fn(journey_id, stage_id, company="marschkamp", bia=None):
    js = _journeys_map()
    j = js.get(journey_id)
    if not j:
        return {"error": "not_found", "message": f"No journey '{journey_id}'.",
                "available_journeys": [x.id for x in js.values()]}
    # Resume affordance: a human naturally says "resume at Stage 6", while the journey engine
    # normally advances from an opaque current-stage id. Accept the human number directly so
    # clients never have to ask users for an internal cursor. Named ids keep their legacy
    # meaning (the completed current stage whose `next` should be returned).
    stage_number = re.fullmatch(r"\s*(?:stage\s*)?(\d+a?)\s*", str(stage_id), re.IGNORECASE)
    if stage_number:
        # The human number is the label IN THE NAME ("Stage 3a · …"): since 2026-08-16 the
        # names run 1, 2, 3, 3a, 4, 5 over six positions, so position-based lookup sent
        # "continue with Stage 4" to the owner loop. Position is the fallback for journeys
        # whose names carry no label.
        label = stage_number.group(1).lower()
        labels = {journey_engine._stage_label(st.name): st for st in j.stages}
        labels.pop(None, None)
        target = labels.get(label) if labels else (
            j.stages[int(label) - 1] if label.isdigit() and 1 <= int(label) <= len(j.stages) else None)
        if target is None:
            human = [journey_engine._stage_label(st.name) or str(i) for i, st in enumerate(j.stages, 1)]
            return {"error": "not_found",
                    "message": f"There is no stage {label} in '{journey_id}'; the stages are "
                               + ", ".join(human) + ".",
                    "stage_numbers": human}
        payload = journey_engine.render_stage_tool(j, target, j.stage_index(target.id) + 1,
                                                   len(j.stages))
        payload["resumed"] = True
        return journey_engine.keep_voice_last(payload)
    cur = j.stage(stage_id)
    if not cur:
        return {"error": "not_found", "message": f"No stage '{stage_id}' in '{journey_id}'.",
                "stage_ids": [s.id for s in j.stages]}
    company = (str(company).strip() if company else "") or "marschkamp"
    gate = _advance_gate_error(cur, j.stage_index(cur.id) + 1, company,
                               (str(bia).strip() if bia else "") or None)
    if gate:
        return gate
    if not cur.next:
        return {"journey_id": j.id, "stage_id": stage_id, "done": True,
                "message": "Journey complete. Final human approval gate applies before you act."}
    nxt = j.stage(cur.next)
    return journey_engine.render_stage_tool(j, nxt, j.stage_index(nxt.id) + 1, len(j.stages))


def get_prompt_template_fn(task, risk_level=None):
    idx = _idx()
    results = idx.search(task, output_type="prompt", risk_level=risk_level, limit=3)
    if not results:
        results = [c for c in idx.search(task, limit=3) if c.section_type == "prompt"] or \
                  idx.search(task, limit=1)
    if not results:
        return {"error": "not_found", "message": f"No prompt template found for task '{task}'."}
    return {"task": task, "count": len(results), "templates": [
        {"id": c.id, "title": c.breadcrumb, "text": c.text, "url": c.url,
         "risk_level": c.risk_level, "mode": c.mode, "controls": c.related_controls}
        for c in results
    ]}


_personas: dict | None = None


def _personas_map():
    global _personas
    if _personas is None:
        _personas = journey_engine.load_personas()
    return _personas


def journeys_catalog():
    js = _journeys_map()
    return {"journeys": [
        {"id": j.id, "title": j.title, "persona": j.persona,
         "when_to_use": j.when_to_use, "stage_count": len(j.stages)} for j in js.values()
    ]}


def personas_catalog():
    return {"personas": [
        {"id": p.get("id", ""), "name": p.get("name", ""), "voice": p.get("voice", ""),
         "default_journey": p.get("default_journey", "")} for p in _personas_map().values()
    ]}


def journey_detail(journey_id):
    j = _journeys_map().get(journey_id)
    if not j:
        return {"error": "not_found", "message": f"No journey '{journey_id}'."}
    return {"id": j.id, "title": j.title, "persona": j.persona, "when_to_use": j.when_to_use,
            "stages": [{"id": s.id, "goal": s.goal} for s in j.stages]}


def journey_prompt_text(journey_id):
    j = _journeys_map().get(journey_id)
    if not j:
        return f"No journey '{journey_id}'."
    return journey_engine.render_stage_prompt(j, j.first_stage(), 1, len(j.stages))


# Set by start_journey, cleared with it. ponytail: process-global, like graph_files._reads_seen
# and with the same ceiling — two concurrent journeys share it. Both journeys on this server run
# the same one today, so the shared value is the right value; key it per session when concurrent
# runs of DIFFERENT journeys become real (tracked in the future-plans note with the read-credit
# twin, which is the same root cause).
_active_risk_task: str | None = None


def set_risk_task(task: str | None) -> None:
    global _active_risk_task
    _active_risk_task = task or None


def forget_risk_task() -> None:
    set_risk_task(None)


def identify_ai_risks_fn(task_description):
    # A running journey has already said what its work is; the model does not need to be
    # trusted to describe it, and the retrieval is only as stable as that description.
    task_description = _active_risk_task or task_description
    idx = _idx()
    governance = idx.search(task_description, limit=4)
    relevant_dnu = [c for c in idx.search(task_description, limit=3) if c.section_type == "do_not_use"]
    combined = list({c.id: c for c in (governance + relevant_dnu)}.values())
    risk_order = {"critical": 4, "high": 3, "medium": 2, "low": 1, "": 0}
    max_risk = max((risk_order.get(c.risk_level, 0) for c in combined), default=0)
    overall_risk = {4: "critical", 3: "high", 2: "medium", 1: "low", 0: "low"}[max_risk]
    return {
        "task": task_description, "risk_level": overall_risk,
        "applicable_controls": list(dict.fromkeys(ctrl for c in combined for ctrl in c.related_controls)),
        "do_not_use_warnings": [{"id": c.id, "title": c.breadcrumb, "summary": c.text[:400]}
                                for c in combined if c.section_type == "do_not_use"],
        "cited_sections": [{"id": c.id, "title": c.breadcrumb, "url": c.url} for c in combined],
    }
