"""Pure-Python tool bodies behind the MCP server."""

from __future__ import annotations
import json
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


# Backlog §B.9, built 2026-08-23 after the first post-§A.2 live run still cost 6 presses:
# six instructions failed to make the model read before drafting, and the write jaw's refusal
# turns recovery into approval presses. So the server serves stage 1's material inside the
# payload and grants read credit for what it served — the first draft is grounded by
# construction and the unread-source refusal structurally cannot fire on stage 1. The cap is
# sized to what the per-stage payload budget already tolerates (stage 1 static 12,184 chars
# against the 14,360 the largest stage ships); truncation and parse/fetch failures both fail
# toward NO credit, so the gate protects exactly as before whenever serving did not happen.
# Sized top-down from the payload budget, not bottom-up from hope: the static budget anchor
# pins every stage at 14,500 chars and stage 1 renders at 12,204 (re-measured 2026-08-24
# after the guide prescription landed), so the runtime digest may spend 14,500 − 12,204.
# The cap counts EVERYTHING appended — headers included; the first accounting missed them
# and the real register was silently dropped 9 chars short. Measured 2026-08-23 against the
# real register (33 activities): ~2,264 all-in. Growth past ~40 activities trips the
# fail-closed no-credit branch — revisit the format then, not the cap. A stage-1 prompt
# edit that grows the static render must re-run this arithmetic in the same commit.
DIGEST_MAX_CHARS = 2296
_DIGEST_HEADER = "\n\nCompany data for this stage, read live at journey start:\n"
_REGISTER_HEADER = ("Recorded activities by department (tier, MTPD) — propose from these, "
                    "never invent:\n")


def _method_summary(content):
    m = json.loads(content)
    parts = []
    if m.get("version") or m.get("method_version"):
        parts.append(f"Method version {m.get('version') or m.get('method_version')}")
    names = [re.sub(r"\s*\([^)]*\)", "", str(s.get("name") or s.get("id")))
             for s in m.get("scenarios", []) if isinstance(s, dict)]
    if names:
        parts.append(f"{len(names)} impact categories: {', '.join(names)}")
    if m.get("time_horizons"):
        parts.append(f"horizons: {', '.join(m['time_horizons'])}")
    if m.get("intolerability_threshold") is not None:
        parts.append(f"intolerability threshold {m['intolerability_threshold']}")
    if not parts:
        raise ValueError("method.json carries none of the expected keys")
    return "Approved method — " + "; ".join(parts) + "."


# A short clock token inside free-prose MTPD text ("clocks open within 0-4 h of an outage"
# -> "0-4 h"). The real register records MTPDs as sentences; serving the sentence would spend
# the whole cap on prose the scope card only needs the number from.
_CLOCK_RE = re.compile(r"[≈~]?\s?\d+(?:\s?[–-]\s?\d+)?\s?(?:h\b|d\b|day s?\b|days?\b|weeks?\b)")


def _register_activity_lines(content):
    """One line per DEPARTMENT (name once, activities folded in), carrying the ranking data
    stage 1's next_moves demand: tier (the supplying asset's criticality) and a short MTPD
    clock per activity. Names only would invite fabricated rankings (adversarial review,
    amendment 1). Compressed to survive the real register — measured 2026-08-23: the naive
    one-line-per-activity form with asset names and full MTPD prose hit 5,687 chars against
    the 2,000 cap, and the fail-closed rule then dropped the register entirely. Asset names
    are deliberately absent: they are stage-3 material, and the register itself is one read
    away when the model wants them."""
    reg = json.loads(content)
    assets = {k: v for k, v in reg.items() if isinstance(v, dict)}  # dep_graph's own shape rule
    by_dept = {}
    for a in assets.values():
        for c in a.get("consumers") or []:
            if not isinstance(c, dict) or not c.get("activity"):
                continue
            dept = str(c.get("dept") or "unassigned")
            acts = by_dept.setdefault(dept, {})
            if c["activity"] in acts:
                continue
            tags = []
            if a.get("criticality") is not None:
                tags.append(f"tier {a['criticality']}")
            clock = _CLOCK_RE.search(str(c.get("consumer_mtpd") or ""))
            if clock:
                # the header's "(tier, MTPD)" legend carries the label; the "≈" carries nothing
                tags.append(clock.group(0).strip().lstrip("≈~").strip())
            acts[c["activity"]] = f" ({', '.join(tags)})" if tags else ""
    if not by_dept:
        raise ValueError("register carries no consumer activities")
    return [f"- {dept}: " + "; ".join(f"{act}{tag}" for act, tag in sorted(acts.items()))
            for dept, acts in sorted(by_dept.items())]


def _stage1_digest(company, stage):
    """(digest_text, credited_paths) for the stage's requires_reads, or ("", []).

    Credit is granted per path ONLY when that path's content made it into the digest — the
    same standard graph_files applies to reads (content that reached the model counts,
    referee-internal reads never did). Any fetch/parse failure or a cap that would drop a
    source entirely fails toward no digest line and no credit for that path, leaving the
    write jaw to protect exactly as it does today."""
    sections, credited = [], []
    budget = DIGEST_MAX_CHARS - len(_DIGEST_HEADER)
    for path in getattr(stage, "requires_reads", []):
        try:
            got = _fetch_artifact(company, path)
            if "error" in got:
                continue
            if path.endswith("method.json"):
                text = _method_summary(got.get("content", ""))
            elif path.endswith("dependency-register.json"):
                text = _REGISTER_HEADER + "\n".join(_register_activity_lines(got.get("content", "")))
            else:
                continue  # a path this builder has no shape for earns no credit
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            continue
        if len(text) + 1 > budget:
            continue  # amendment 2: a source the cap cannot fit is unserved — no credit
        sections.append(text)
        credited.append(path)
        budget -= len(text) + 1
    if not sections:
        return "", []
    return _DIGEST_HEADER + "\n".join(sections), credited


def start_journey_fn(journey_id, company=None):
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
    set_risk_task(j.risk_task)   # its governance classification is the journey's, not the model's
    s = j.first_stage()
    payload = journey_engine.render_stage_tool(j, s, 1, len(j.stages))
    payload["overview"] = j.when_to_use
    payload["total_stages"] = len(j.stages)
    # §A.17: the digest follows the company the user is IN. Absent param = the pre-room
    # behaviour (resolved default), so the un-republished manifest keeps its exact lane.
    company = graph_files.resolve_company(company or graph_files.DEFAULT_COMPANY)
    if company and payload.get("copy_paste_prompt"):
        digest, credited = _stage1_digest(company, s)
        if digest:
            payload["copy_paste_prompt"] += digest
            for path in credited:
                graph_files.note_read(company, path)
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


def _advance_gate_error(stage, stage_num, company, bia=None, found=None):
    """P7 I-1 part 2: a named completed stage advances only when its canonical artifacts
    are saved and meet the journey-owned contract — closes the referent-substitution
    route (I-1 as fired) and the skipped-artifact class (I-5). Pattern paths ('*') are
    write-time contracts only: the owner side-quest's N/A branch is register-dependent
    and not server-derivable. Fails CLOSED on a data-source outage, legibly.
    `<bia>` paths are read in the folder the agent names (bia=), never guessed.
    `found`, when a list, collects each conforming document's facts (§A.16): the gate is
    the only place that knows the advance passed BECAUSE the document already exists."""
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
        if found is not None:
            found.append({"name": name, "path": path, "size": size,
                          "saved": got.get("modified") or "unknown"})
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
    # §A.16: only the FIRST stage's advance collects found-document facts — that is the turn
    # every re-run walks through (the incident's silent skip), and later cards have no room
    # (stage 4 renders 140 chars under the 14,500 anchor the digest arithmetic protects).
    found: list = [] if j.stage_index(cur.id) == 0 else None
    gate = _advance_gate_error(cur, j.stage_index(cur.id) + 1, company,
                               (str(bia).strip() if bia else "") or None, found=found)
    if gate:
        return gate
    if not cur.next:
        return {"journey_id": j.id, "stage_id": stage_id, "done": True,
                "message": "Journey complete. Final human approval gate applies before you act."}
    nxt = j.stage(cur.next)
    payload = journey_engine.render_stage_tool(j, nxt, j.stage_index(nxt.id) + 1, len(j.stages))
    if found:
        # Facts server-side, condition model-side: there is no session id (settled, §7 of the
        # tracker), so only the model can tell "just saved by this user" from "left over from
        # an earlier run" — it has the conversation. Positive conditional, refusal-text lane.
        payload["already_saved"] = {
            "documents": found,
            "note": ("If the user saved this document in this conversation, carry on. "
                     "Otherwise say what was found and when it was saved, then offer: open "
                     "the saved document and carry on, redo the stage, or pick another "
                     "process."),
        }
        payload = journey_engine.keep_voice_last(payload)
    return payload


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


# journeys_catalog / personas_catalog / journey_detail / journey_prompt_text (and the
# _personas memo) went with the MCP prompt+resource surface on 2026-08-24 — each had exactly
# one caller, its own registration in server.py, and nothing read those registrations.
# `design/personas.json` itself stays: voice_check.py reads it directly for the bad-example
# needles, which is a live consumer.


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
