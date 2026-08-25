"""Stage-1 interview-guide content control + printable A4 rendering.

Two halves sharing one parser, built 2026-08-24 after the first post-§B.9 live run saved a
hollow guide (backlog §A.8, the predicted regression):

- `problems()` — the write-jaw check. Hans's accepted server test, verbatim from his ruling
  (docs/2026-08-23-stages-2-6-audit.md): "count sentences ending in a question mark, count
  register ids quoted inside the dependency question, check the six horizon strings appear
  literally, check a three-question short version exists, check the bring-list names files —
  fail on any of those being zero." Deterministic counts and literal-string presence only —
  byte floors and keyword/semantic matching are refused control styles (H1, §A.8).
  The 6-12-questions-per-activity band is writing guidance in the stage-1 prompt, NEVER
  enforced (owner ruling 2026-08-24, pinned by test_a_good_thirteen_question_guide_is_not_
  refused). ponytail: the band's upgrade seam is a per-block `?` count in `problems()`.
- `render()`/`publish()` — the print view a BCM manager clicks from the save receipt and
  prints: the guide section only, A4, per-activity blocks that never split across pages.
"""
from __future__ import annotations

import hashlib
import html
import json
import sys
import time
from pathlib import Path

# Must match the stage-1 copy_paste_prompt prescription in design/run-bia.yaml — the yaml
# lives in the independently edited public design repo, so the coupling is pinned by
# test_stage1_prescribes_the_headings_the_jaw_enforces.
SHORT_MARKER = "### Short version"
BRING_MARKER = "### Bring to the interview"

# Fixed room paths, real files every company folder carries (ms-agent-install.md Part A's
# folder layout, uploaded once per company) — naming them needs no extra read, so the
# bring-list refusals can teach with them same as any other check in this module degrades:
# always available, company-specific data or not. Teach-in-the-refusal pattern proven live
# 2026-08-24 for the structural check (§A.8); this closes the other measured wasted press
# (calls-2026-W35 19:23:18/59 — the redraft left the block empty for lack of names, not intent).
_BRING_CANDIDATES = (" Documents this room's folders carry: 08_Prior-Cycle/ (last cycle's "
                     "BIA), 04_Suppliers/supplier-sla.md, 02_BCM-Method/impact-criteria.md "
                     "(method.json's own reference for its thresholds).")

# Renderer provenance for the page footer. ponytail: import-time sha of this source, not the
# live compare dep_graph runs (dep_graph.py:25-56) — the restart is part of every deploy
# round; upgrade to the live STALE banner if a layout iteration ever ships mid-service again.
_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:8]


def _guide_section(content: str) -> str | None:
    """The text after the '## Interview guide' heading line, up to the next '## ' or EOF."""
    for chunk in ("\n" + content.replace("\r\n", "\n")).split("\n## ")[1:]:
        heading, _, body = chunk.partition("\n")
        if heading.strip().casefold() == "interview guide":
            return body
    return None


def _blocks(section: str) -> list[tuple[str, str]]:
    """(heading, body) per '### ' block; text before the first block is dropped."""
    out = []
    for chunk in ("\n" + section).split("\n### ")[1:]:
        heading, _, body = chunk.partition("\n")
        out.append((heading.strip(), body))
    return out


def _activity_ids(register_text: str | None) -> dict[str, set[str]]:
    """casefolded activity wording -> ids of the assets that list it as a consumer.
    {} on any failure — a company without a readable register still saves stage 1
    (the same degrade rule the unread-source check applies)."""
    try:
        reg = json.loads(register_text or "")
        out: dict[str, set[str]] = {}
        for aid, a in reg.items():
            if not isinstance(a, dict):
                continue
            for c in a.get("consumers") or []:
                if isinstance(c, dict) and c.get("activity"):
                    out.setdefault(str(c["activity"]).casefold(), set()).add(aid)
        return out
    except (ValueError, AttributeError, TypeError):
        return {}


def _horizons(method_text: str | None) -> list[str]:
    try:
        hs = json.loads(method_text or "").get("time_horizons") or []
        return [h for h in hs if isinstance(h, str) and h.strip()]
    except (ValueError, AttributeError, TypeError):
        return []


def _activities_listing(register_text: str | None) -> str:
    """'dept — activity (ids); activity (ids)' per department, register wording verbatim.
    "" on any failure — the refusal it enriches stands on its own without it."""
    try:
        by_dept: dict[str, dict[str, list[str]]] = {}
        for aid, a in json.loads(register_text or "").items():
            if not isinstance(a, dict):
                continue
            for c in a.get("consumers") or []:
                if isinstance(c, dict) and c.get("activity"):
                    dept = str(c.get("dept") or "unassigned")
                    ids = by_dept.setdefault(dept, {}).setdefault(str(c["activity"]), [])
                    if aid not in ids:
                        ids.append(aid)
        return "\n  ".join(
            f"{dept} — " + "; ".join(f"{act} ({', '.join(ids)})"
                                     for act, ids in sorted(acts.items()))
            for dept, acts in sorted(by_dept.items()))
    except (ValueError, AttributeError, TypeError):
        return ""


def problems(content: str, method_text: str | None, register_text: str | None) -> list[str]:
    """Every problem with the guide section, batched (the _one_refusal contract). Pure —
    the callers fetch; unavailable sources skip their checks, never block."""
    section = _guide_section(content)
    if section is None:
        return ["write refused: '## Interview guide' is not a heading of its own line — "
                "make it one, then retry."]
    out: list[str] = []
    if section.count("?") == 0:
        out.append("write refused: the ## Interview guide section contains no questions — "
                   "a guide is questions to ask in the room; write them out, then retry.")
    blocks = _blocks(section)
    short = next((b for h, b in blocks if h.casefold().startswith("short version")), None)
    if short is None:
        out.append(f"write refused: no '{SHORT_MARKER}' block in the guide — add the "
                   "three-question short set, then retry.")
    elif short.count("?") == 0:
        out.append("write refused: the Short version block carries no questions — it is "
                   "the twenty-minute set; write its three questions, then retry.")
    bring = next((b for h, b in blocks if h.casefold().startswith("bring to the interview")),
                 None)
    if bring is None:
        out.append(f"write refused: no '{BRING_MARKER}' block in the guide — list the "
                   "documents the interviewee should bring, by name, then retry."
                   + _BRING_CANDIDATES)
    elif not any(line.strip().startswith("- ") for line in bring.splitlines()):
        out.append("write refused: the Bring to the interview block lists nothing — name "
                   "the actual documents as '- ' items, then retry." + _BRING_CANDIDATES)

    acts = _activity_ids(register_text)
    if acts:
        recognized = [(h, b, ids) for h, b in blocks
                      for a, ids in acts.items() if a in h.casefold()]
        if not recognized:
            # Live 2026-08-24 06:43:19→06:43:55 (calls-2026-W35): this refusal masks the
            # per-block id/horizon checks, so without the listing the agent needs a SECOND
            # refusal to learn the asset ids — a structurally guaranteed wasted press.
            # Lesson #26 again: enumeration lives in the write jaw. Ids ride here and not in
            # the §B.9 digest because the digest's cap arithmetic cannot fund ~400 chars of
            # ids (2,264 measured + 406 against cap 2,296) and every static-payload lever is
            # ruled out (connector_guidance stays unspent; the 14,500 pin is never relaxed).
            msg = ("write refused: no guide block is headed with a register activity — "
                   "head each activity's block with the register's exact activity wording "
                   "and name its asset ids in the dependency question.")
            listing = _activities_listing(register_text)
            if listing:
                msg += " The register records:\n  " + listing
            hz = _horizons(method_text)
            if hz:
                msg += ("\nWrite the method horizons (" + ", ".join(hz) +
                        ") into each block's impact question verbatim, then retry.")
            else:
                msg += "\nThen retry."
            out.append(msg)
        for heading, body, ids in recognized:
            missing = sorted(i for i in ids if i not in body)
            if missing:
                out.append(f"write refused: the dependency question for '{heading}' names "
                           f"{len(ids) - len(missing)} of its {len(ids)} register assets "
                           f"({', '.join(missing[:6])} missing) — name every asset this "
                           "activity consumes, then retry.")
            if body.count("?") == 0:
                out.append(f"write refused: the block '{heading}' carries no questions — "
                           "every activity block is interview questions, then retry.")
            gone = [h for h in _horizons(method_text) if h not in body]
            if gone and _horizons(method_text):
                out.append(f"write refused: the impact question for '{heading}' does not "
                           f"write out the method's time horizons ({', '.join(gone[:6])} "
                           "missing) — the interviewee answers what they hear; write the "
                           "horizons into the question verbatim, then retry.")
    return out


def counts(content: str) -> tuple[int, int]:
    """(questions, activities) over the guide's activity blocks — the receipt's 'N questions
    across M activities', Hans's 'the thing that tells me the guide is real without opening
    it'. The short set and bring list are structure, not interview volume, so they stay out."""
    section = _guide_section(content) or ""
    acts = [(h, b) for h, b in _blocks(section)
            if not h.casefold().startswith(("short version", "bring to the interview"))]
    return sum(b.count("?") for _, b in acts), len(acts)


# ── the printable page ───────────────────────────────────────────────────────────────
# Page-local print CSS appended to the composed brand sheet (the build_guide_page idiom:
# compose STYLE, never paste tokens — tests/test_brand.py's rule). Type sizes come from the
# brand token scale only. Page count is an output, never pinned: blocks declare
# break-inside and the paper takes what it takes. The paper affordances are Hans's page
# ruling (2026-08-24, #bia-workflow, verbatim in docs/2026-08-23-stages-2-6-audit.md):
# short set first, writing lines, the empty horizons grid, owner+deputy from the register,
# attendance blanks, a per-block confirmation line, page numbers.
EXTRA = """
body{max-width:820px;margin:0 auto;padding:24px}
h1{font-size:var(--t-heading-sm);line-height:var(--lh-heading-sm)}
.meta{font-size:var(--t-caption);line-height:var(--lh-caption);color:var(--ink-soft,#666);
  margin-bottom:12px}
.record{font-size:var(--t-body-sm);margin:0 0 20px;border:1px solid var(--line,#bbb);
  padding:8px 12px}
.qblock{break-inside:avoid;margin-bottom:20px}
.qblock h2{font-size:var(--t-subheading);line-height:var(--lh-subheading);margin:0 0 2px}
.owner{font-size:var(--t-body-sm);color:var(--ink-soft,#444);margin:0 0 8px}
.wline{border-bottom:1px solid var(--line,#bbb);height:7mm}
.grid{border-collapse:collapse;width:100%;margin:4px 0}
.grid th,.grid td{border:1px solid var(--line,#999);padding:3px 6px;
  font-size:var(--t-caption)}
.grid td{height:10mm}
.confirm{margin-top:10px;font-size:var(--t-body-sm)}
.printbar{margin:16px 0}
.foot-prov{font-size:var(--t-caption);color:var(--ink-soft,#666);margin-top:32px;
  border-top:1px solid var(--line,#ddd);padding-top:8px}
@page{size:A4;margin:12mm;
  @bottom-center{content:"page " counter(page) " of " counter(pages)}}
@media print{.noprint{display:none}body{max-width:none;padding:0}}
"""

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{title}</title>
<style>{style}</style></head>
<body>
<h1>{title}</h1>
<p class="meta">{meta}</p>
<p class="record">Date ________ &nbsp; Start ________ &nbsp; End ________ &nbsp;
In the room: ______________________________ &nbsp; Led by: ________________</p>
<div class="printbar noprint"><button onclick="print()">Print this guide</button></div>
{body}
<p class="foot-prov">{prov}</p>
</body></html>
"""

_WLINES = '<div class="wline"></div>' * 4
_CONFIRM = ('<p class="confirm">Seen and corrected by ______________________ '
            'date ____________</p>')


def _owners(register_text: str | None) -> dict[str, list[str]]:
    """casefolded activity -> ['Owner (deputy: X)'] lines from the supplying assets'
    owner_name/stellvertreter. {} on any failure — names are a courtesy on the page."""
    try:
        reg = json.loads(register_text or "")
        out: dict[str, list[str]] = {}
        for a in reg.values():
            if not isinstance(a, dict) or not a.get("owner_name"):
                continue
            line = a["owner_name"] + (f" (deputy: {a['stellvertreter']})"
                                      if a.get("stellvertreter") else "")
            for c in a.get("consumers") or []:
                if isinstance(c, dict) and c.get("activity"):
                    key = str(c["activity"]).casefold()
                    if line not in out.setdefault(key, []):
                        out[key].append(line)
        return out
    except (ValueError, AttributeError, TypeError):
        return {}


def _grid(horizons: list[str]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in horizons)
    cells = "<td></td>" * len(horizons)
    return f'<table class="grid"><tr>{head}</tr><tr>{cells}</tr></table>'


def _paperize(body_html: str, grid: str) -> str:
    """Writing space under every question; the empty horizons grid under the first (the
    impact-over-time question, per the prescription's fixed order)."""
    idx = body_html.find("</li>")
    if idx < 0:
        return body_html + _WLINES
    head, tail = body_html[:idx], body_html[idx:]
    first = grid if grid else _WLINES
    return head + first + tail.replace("</li>", _WLINES + "</li>").replace(
        _WLINES + "</li>", "</li>", 1)


def render(content: str, company: str, bia: str,
           register_text: str | None = None, method_text: str | None = None) -> str:
    """The guide section as a standalone print page — what goes into the interview room.
    Everything else in the saved document (scope, risk, method parameters) deliberately
    stays off the page. Order per Hans's ruling: short set first, then the activity
    blocks, then the bring list."""
    from build_kb_pages import STYLE, render_markdown

    section = _guide_section(content) or ""
    owners = _owners(register_text)
    grid = _grid(_horizons(method_text)) if _horizons(method_text) else ""
    shorts, activities, brings = [], [], []
    for heading, body in _blocks(section):
        h_cf = heading.casefold()
        if h_cf.startswith("short version"):
            shorts.append('<div class="qblock"><h2>%s</h2>%s</div>'
                          % (html.escape(heading),
                             _paperize(render_markdown(body), "")))
        elif h_cf.startswith("bring to the interview"):
            brings.append('<div class="qblock"><h2>%s</h2>%s</div>'
                          % (html.escape(heading), render_markdown(body)))
        else:
            named = next((lines for act, lines in owners.items() if act in h_cf), [])
            owner_html = ('<p class="owner">Owner: %s</p>' % html.escape("; ".join(named))
                          if named else "")
            activities.append(
                '<div class="qblock"><h2>%s</h2>%s%s%s</div>'
                % (html.escape(heading), owner_html,
                   _paperize(render_markdown(body), grid), _CONFIRM))
    title = f"Interview guide — {bia.replace('-', ' ').title()}"
    meta = html.escape(
        f"{company} · output/{bia}/stage1-scope-and-guide.md · "
        f"{len(content.encode('utf-8')):,} bytes · " + time.strftime("%Y-%m-%d"))
    prov = html.escape(f"Rendered by the BIA-Workflow from the saved stage-1 document · "
                       f"renderer {_SHA}")
    return _PAGE.format(title=html.escape(title), style=STYLE + EXTRA,
                        body="\n".join(shorts + activities + brings), meta=meta, prov=prov)


def publish(company: str, bia: str, data: bytes,
            register_text: str | None = None, method_text: str | None = None) -> str:
    """Write the print page under the graph public tree (the one directory the service may
    write and nginx already serves) and return its URL. dep_graph.PUBLIC is resolved at
    call time so the test suite's existing autouse tmp redirect covers this writer too.
    Raises on failure — the caller is a never-blocks hook."""
    import dep_graph

    out = dep_graph.PUBLIC / company / bia / "guide.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data.decode("utf-8"), company, bia,
                          register_text=register_text, method_text=method_text),
                   encoding="utf-8")
    return f"{dep_graph.BASE_URL}/{company}/{bia}/guide.html"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(f"usage: {sys.argv[0]} <stage1.md> [company] [bia]")
    md = Path(sys.argv[1]).read_text(encoding="utf-8")
    print(render(md, sys.argv[2] if len(sys.argv) > 2 else "company",
                 sys.argv[3] if len(sys.argv) > 3 else "process"))
