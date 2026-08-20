#!/usr/bin/env python3
"""Build the public BCM-manager guide at /demo/bia-workflow-guide/.

This page is the canonical guide (the repo markdown draft was archived once it
shipped). Reuses build_kb_pages.STYLE so it looks like the knowledge base, renders
the Excalidraw scene to inline SVG in a proportional face, and reads
data/chunks.json + server.py live so the counts can't go stale. Writes
/var/www/addendum-demo/bia-workflow-guide/ — served by the existing nginx /demo/
block: no sudo, no service restart, idempotent.

CLI: .venv/bin/python build_guide_page.py [--out DIR]
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
from pathlib import Path

import brand
from build_kb_pages import STYLE

APP = Path(__file__).resolve().parent
# The chunks follow the one DATA_DIR knob (/srv/addendum/data on brain, <checkout>/data locally).
DATA_DIR = Path(os.environ.get("BIA_WORKFLOW_DATA_DIR", APP / "data"))
DEFAULT_OUT = Path("/var/www/addendum-demo/bia-workflow-guide")
SCENE = APP / "docs" / "bia-workflow-manager-guide.excalidraw"
SANS = "Arial, Helvetica, sans-serif"
# The scene above is the only source. A published excalidraw.com "#json=" link used to sit in
# the figure caption, but those links are immutable: it kept serving the pre-2026-07-24 diagram
# (old stage names, an "in Teams" title) while this page rendered the current one. Removed
# rather than left to drift. Re-add only as a link you can actually update.
# The public guide must name the PUBLIC agent's data room. There is only one room as of
# 2026-08-10: the marschkamp-demo copy had drifted into an unusable stale snapshot and was
# archived, and both agents now work `marschkamp` (KG's call). So this constant no longer
# separates anything — it just has to match the public agent's Part D token, or the guide
# tells readers to type a company the agent will refuse.
COMPANY = "marschkamp"

EXTRA = """
.lede{font-size:var(--t-subheading);line-height:var(--lh-subheading);letter-spacing:var(--ls-subheading)}
h2{font:var(--t-caption)/var(--lh-caption) var(--sans);letter-spacing:var(--ls-caption);
text-transform:uppercase;color:var(--ink-2);
margin:3rem 0 1.1rem;padding-bottom:.55rem;border-bottom:2px solid var(--ink);scroll-margin-top:1rem}
h3{font:700 var(--t-subheading)/var(--lh-subheading) var(--sans);
letter-spacing:var(--ls-subheading);color:var(--ink);margin:1.6rem 0 .5rem}
.say{background:var(--tint);border:1px solid var(--rule);border-left:6px solid var(--accent);
border-radius:var(--r-card);padding:.85rem 1rem;margin:.5rem 0 1.4rem;
font:.95rem/1.6 var(--mono);color:var(--ink);white-space:pre-wrap;word-wrap:break-word}
.say b{display:block;font:.72rem/1.5 var(--sans);letter-spacing:.02em;text-transform:uppercase;
color:var(--ink-3);margin-bottom:.45rem;font-weight:700}
table{border-collapse:collapse;width:100%;margin:0 0 1.3rem;font-size:.95rem}
th,td{text-align:left;vertical-align:top;padding:.55rem .7rem;border-bottom:1px solid var(--rule)}
th{font:.72rem/1.5 var(--sans);letter-spacing:.02em;text-transform:uppercase;color:var(--ink-3);font-weight:700}
td.n{font:700 .84rem/1.6 var(--sans);font-variant-numeric:tabular-nums;color:var(--accent);width:2.2rem}
.stats{display:flex;flex-wrap:wrap;gap:.6rem;margin:0 0 1.4rem;padding:0;list-style:none}
.stats li{padding:.7rem .8rem;margin:0;flex:1 1 7rem;background:var(--tint);
border:1px solid var(--rule);border-radius:var(--r-card)}
.stats li::before{display:none}
.stats b{display:block;font:700 var(--t-heading-sm)/var(--lh-heading-sm) var(--sans);
letter-spacing:var(--ls-heading-sm);color:var(--accent)}
.stats span{font:.72rem/1.5 var(--sans);letter-spacing:.02em;text-transform:uppercase;color:var(--ink-3)}
.cols{columns:2;column-gap:1.6rem;margin:0 0 1.3rem}
.cols li{padding-left:0;margin:0 0 .5rem;break-inside:avoid;font-size:.9rem;line-height:1.45}
.cols li::before{display:none}
.cols a{text-decoration:none;border-bottom:1px solid transparent}
.cols a:hover{border-bottom-color:var(--accent)}
.note{background:var(--tint);border:1px solid var(--rule);border-left:6px solid var(--rule);
border-radius:var(--r-card);
padding:.9rem 1.1rem;margin:0 0 1.3rem;font-size:.95rem;color:var(--ink-2)}
.note strong{color:var(--ink)}
.plan{display:block;width:100%;height:auto;background:var(--tint);border:1px solid var(--rule);
border-radius:var(--r-card)}
figure{margin:0 0 1.4rem}
/* The plan now opens the page — h2's 3rem top margin would leave a hole under the meta rule. */
h2.lead-plan{margin-top:.4rem}
/* One column for everything, wider than the KB default so the plan and the prose share an
   edge instead of the plan breaking out. ~95 characters a line is past the comfortable
   reading range; that is the accepted cost of a single aligned edge (KG, 2026-07-24). */
.wrap{max-width:52rem}
"""

STAGES = [
    # (number, name, what it produces, what you do) — the five traditional-BIA names KG fixed
    # with Willem on 2026-08-13; the owner loop is "3a" so this page, the deck and the agent's
    # stage card agree on five. Source of the names: design/run-bia.yaml `name:`.
    ("1", "Identification of scope",
     "Department, head, headcount, sites; key activities; the fixed method; the interview guide",
     "Confirm the process, approve the document"),
    ("2", "Structured interview (conversational)",
     "The interview — asked, challenged, explained — written up with its open points",
     "Check it against what was said, approve"),
    ("3", "Convert to the standardised template",
     "Impact over time, resource requirements, dependencies, owner gaps",
     "Decide what it flags, approve"),
    ("3a", "Missing-owner loop",
     "A missing owner recorded in the register — only when one is missing",
     "Give it the facts, sign off by name"),
    ("4", "List the requirements (RTO, MTPD, RPO)",
     "One card per activity: grid, MTPD, RTO, RPO, resources over time, quotes",
     "Approve the card, then three documents"),
    ("5", "Consolidate + sanity check → handover",
     "The requirements list, checked for plausibility, for solution design",
     "Approve. The BIA ends here"),
]

PROMPTS = [
    ("Start", f"Start a BIA for {COMPANY}. Take me through it one stage at a time."),
    ("Continue, when it announces the next step and waits", "Proceed"),
    ("Approve and save",
     "Approved, save exactly this, complete and identical to what you presented, every "
     "section included, to the path you proposed. Then continue with the next stage."),
    ("Amend, then approve",
     "Amend the review, then approved: add the wastewater dependency, and correct the CO2 "
     "evidence. The interview never states an eight-hour target for it."),
    ("Sign off a register change, the one write into shared company data",
     "Approved: the exact register diff as presented, the approved field changes and nothing "
     "else. Sign-off: <your name>, BCM manager, <date>."),
]

EXPECT = [
    ("It waits a lot.",
     "It tells you what it plans to do next, then stops. Answer <code>Proceed</code>. "
     "That was the most frequent message in our whole run."),
    ("It forgets to save.",
     "It'll report a stage as done while nothing was written. Ask it straight: "
     "<em>is the stage document saved? If not, present it for approval.</em>"),
    ("A refusal is the safety net doing its job.",
     "When you see <em>“write refused: … — this looks like a summary, not the approved "
     "artifact. Send the COMPLETE approved content exactly as previewed.”</em>, the system has "
     "just stopped an incomplete document from being saved. The part before the dash names "
     "exactly what was missing, so it is worth reading. Say <code>Proceed</code> and let it "
     "try again."),
    ("It can talk itself into a dead end.",
     "“I cannot do this from here” is usually wrong. Answer: <em>attempt it and report the "
     "exact error you receive, don't report inability without trying.</em> That got us moving "
     "again every time."),
    ("It can bend a number to make its own checks agree.",
     "In our run it dropped a maximum tolerable outage from 24 hours to 8, because its own "
     "scoring said so, against what the process owner had stated on the record. "
     "<strong>Only you catch that one.</strong> When a number moves, ask what evidence moved "
     "with it."),
]

CATCHES = [
    ("Half-saved or summarised documents", "Whether the interviewee would recognise it"),
    ("Quotes that aren't in the interview", "Whether a stage document got saved at all"),
    ("Rewriting the register instead of one entry", "Whether a score matches what the owner said"),
    ("Missing method values, wrong vocabulary", "Whether the story still makes business sense"),
    ("Recovery time longer than the tolerable outage", "Whether an open gap is being quietly closed"),
]

RULES = [
    "<strong>Never approve what you haven't read.</strong> Approval is the whole control.",
    "<strong>Ask “was it saved?”</strong> at every stage boundary.",
    "<strong>Sign the register off by name.</strong> It's the only change to shared company data.",
    "<strong>Gaps stay gaps.</strong> An unsigned contract is an open gap, never a capability.",
    "<strong>Stop at the handover.</strong> If it offers to draft a continuity plan, refuse.",
]


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def scene_to_svg(path: Path, pad: int = 24) -> str:
    """Render the Excalidraw scene as inline SVG in a proportional sans face.

    Covers exactly the element types the scene uses (rounded rectangles, bound and
    standalone multi-line text, straight arrows). Excalidraw's own exporter needs its
    JS runtime and ships the hand-drawn font, neither of which belongs on this page.
    """
    els = json.loads(path.read_text(encoding="utf-8"))["elements"]
    xs = [e["x"] for e in els] + [e["x"] + e["width"] for e in els]
    ys = [e["y"] for e in els] + [e["y"] + e["height"] for e in els]
    minx, miny = min(xs) - pad, min(ys) - pad
    w, h = max(xs) - minx + pad, max(ys) - miny + pad

    heads = sorted({e["strokeColor"] for e in els if e["type"] == "arrow"})
    defs = "".join(
        f'<marker id="h{i}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
        f'markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>'
        for i, c in enumerate(heads))

    out = []
    for e in els:
        t, op = e["type"], e.get("opacity", 100) / 100
        oa = f' opacity="{op:g}"' if op != 1 else ""
        if t == "rectangle":
            fill = e.get("backgroundColor", "transparent")
            fill = "none" if fill == "transparent" else fill
            rx = 12 if (e.get("roundness") or {}).get("type") == 3 else 0
            out.append(f'<rect x="{e["x"]:g}" y="{e["y"]:g}" width="{e["width"]:g}" '
                       f'height="{e["height"]:g}" rx="{rx}" fill="{fill}" '
                       f'stroke="{e["strokeColor"]}" stroke-width="{e.get("strokeWidth", 2)}"{oa}/>')
        elif t == "ellipse":
            fill = e.get("backgroundColor", "transparent")
            fill = "none" if fill == "transparent" else fill
            out.append(f'<ellipse cx="{e["x"] + e["width"] / 2:g}" '
                       f'cy="{e["y"] + e["height"] / 2:g}" rx="{e["width"] / 2:g}" '
                       f'ry="{e["height"] / 2:g}" fill="{fill}" stroke="{e["strokeColor"]}" '
                       f'stroke-width="{e.get("strokeWidth", 2)}"{oa}/>')
        elif t == "arrow":
            (x0, y0), (x1, y1) = e["points"][0], e["points"][-1]
            out.append(f'<line x1="{e["x"] + x0:g}" y1="{e["y"] + y0:g}" '
                       f'x2="{e["x"] + x1:g}" y2="{e["y"] + y1:g}" stroke="{e["strokeColor"]}" '
                       f'stroke-width="{e.get("strokeWidth", 2)}" stroke-linecap="round" '
                       f'marker-end="url(#h{heads.index(e["strokeColor"])})"{oa}/>')
        elif t == "text":
            lines = e["text"].split("\n")
            fs = e["fontSize"]
            lh = fs * e.get("lineHeight", 1.25)
            if e.get("containerId"):
                box = next(c for c in els if c["id"] == e["containerId"])
                anchor, tx = "middle", box["x"] + box["width"] / 2
                top = box["y"] + (box["height"] - len(lines) * lh) / 2
            else:
                anchor, tx, top = "start", e["x"], e["y"]
            weight = ' font-weight="600"' if fs >= 19 else ""
            # xml:space keeps runs of spaces, which SVG would otherwise collapse — without it
            # any alignment held in the scene silently disappears in the browser.
            out.append(f'<text x="{tx:g}" y="{top + fs * 0.92:g}" font-family="{SANS}" '
                       f'font-size="{fs}" fill="{e["strokeColor"]}" xml:space="preserve" '
                       f'text-anchor="{anchor}"{weight}{oa}>' + "".join(
                           f'<tspan x="{tx:g}" dy="{0 if i == 0 else lh:g}">{esc(ln) or " "}</tspan>'
                           for i, ln in enumerate(lines)) + "</text>")

    return (f'<svg class="plan" viewBox="{minx:g} {miny:g} {w:g} {h:g}" '
            f'xmlns="http://www.w3.org/2000/svg" role="img" '
            f'aria-label="One-page flight plan: the five BIA stages (and one loop), the five prompts, '
            f'what to expect, and who catches what.">'
            f"<defs>{defs}</defs>"
            f'<rect x="{minx:g}" y="{miny:g}" width="{w:g}" height="{h:g}" fill="#ffffff"/>'
            + "".join(out) + "</svg>")


def build(out: Path) -> Path:
    chunks = json.loads((DATA_DIR / "chunks.json").read_text(encoding="utf-8"))
    prompts = [c for c in chunks if c.get("section_type") == "prompt"]
    tools = len(re.findall(r"@mcp\.tool", (APP / "server.py").read_text(encoding="utf-8")))
    pps = sorted({c["pp"] for c in chunks if (c.get("pp") or "").startswith("pp")})

    # The flight plan opens the page: it is the one artifact that shows the whole workflow at
    # once, so it earns the hero slot rather than closing the page as a summary.
    p = ['<h2 id="plan" class="lead-plan">The whole thing on one page</h2>'
         "<figure>" + scene_to_svg(SCENE) + "</figure>"]

    # Surface-neutral on purpose: this one guide serves both agents — the private one in
    # Teams and the public one in a browser. Naming Teams here misled every web visitor.
    p.append('<p class="lede">An assistant that runs a Business Impact Analysis '
             'with you, one business process at a time. It reads your evidence, drafts the '
             'analysis, and writes the documents back. At every gate it stops, and you decide.</p>')

    p.append('<div class="note"><strong>Three things hold from start to finish.</strong> '
             'It prepares and you decide: every stage ends with your approval, your amendment, '
             "or your refusal. Nothing is real until it's saved, because chat text isn't a "
             "record. And a BIA never picks a solution; it hands requirements to solution "
             "design (PP4).</div>")

    p.append('<h2 id="start">Before you start</h2><ul>'
             "<li>Open a <strong>new chat for each BIA</strong>. One chat, one process.</li>"
             f"<li>Use the <strong>demo company only</strong> (<code>{COMPANY}</code>). "
             "No real company data, no contracts, no personal information.</li>"
             "<li>Block 60 to 90 minutes. A full run took us around 40 exchanges.</li></ul>")

    p.append('<h2 id="stages">The five stages (and one loop)</h2><table><tr><th></th><th>Stage</th>'
             "<th>What it produces</th><th>What you do</th></tr>")
    for num, name, makes, does in STAGES:
        p.append(f'<tr><td class="n">{num}</td><td><strong>{esc(name)}</strong></td>'
                 f"<td>{esc(makes)}</td><td>{esc(does)}</td></tr>")
    p.append("</table><p>Stage 3a only fires when the analysis hits a dependency nobody owns. "
             "That's the workflow pulling a real gap into the open instead of writing around "
             "it.</p>")

    p.append('<h2 id="prompts">Your five prompts</h2><p>This is the whole vocabulary. '
             "Everything else is normal conversation.</p>")
    for label, text in PROMPTS:
        p.append(f'<div class="say"><b>{esc(label)}</b>{esc(text)}</div>')
    p.append("<p>Two habits do most of the work. Say <strong>“complete and identical, every "
             "section included”</strong> when you approve, because that wording is what stopped "
             "the half-saved documents in our tests. And don't name a tool: approve the change "
             "and let the assistant pick how to do it.</p>")

    p.append('<h2 id="expect">What will actually happen</h2>'
             "<p>We ran a full six-stage BIA on 24 July 2026 and counted every intervention. "
             "Across 37 manager turns there were 38 of them: ten corrective steers, twelve "
             "amendments at the gates, sixteen nudges to carry on. In other words, nearly every "
             "message you send is a correction of some kind. Most landed first try; one save was "
             "refused repeatedly before it came out right. Nothing wrong ever reached the company "
             "files, and the run passed on that count &mdash; but its formal verdict was a "
             "<strong>conditional</strong> pass, not a clean one. Plan for a capable colleague "
             "who needs continuous supervision, not an autopilot.</p><ol>")
    for head, body in EXPECT:
        p.append(f"<li><strong>{esc(head)}</strong> {body}</li>")
    p.append("</ol>")

    p.append('<h2 id="catches">Who catches what</h2><table>'
             "<tr><th>The system catches this</th><th>Only you catch this</th></tr>")
    for machine, human in CATCHES:
        p.append(f"<tr><td>{esc(machine)}</td><td>{human}</td></tr>")
    p.append("</table>")

    p.append('<h2 id="rules">Five rules worth keeping</h2><ol>')
    for r in RULES:
        p.append(f"<li>{r}</li>")
    p.append("</ol>")

    p.append('<h2 id="behind">What sits behind the assistant</h2>'
             "<p>It doesn't improvise BCM method. It answers from the BCI AI Addendum, cites "
             "the section it used, and every citation resolves to a page you can read "
             "yourself.</p>")
    p.append(f'<ul class="stats">'
             f'<li><b>{len(chunks)}</b><span>addendum sections</span></li>'
             f'<li><b>{len(prompts)}</b><span>prompt templates</span></li>'
             f'<li><b>{tools}</b><span>assistant tools</span></li>'
             f'<li><b>{len(pps)}</b><span>PP process phases</span></li></ul>')
    p.append('<p>The whole library sits in <a href="../kb/">the knowledge base</a>: AI support '
             "for each professional practice PP1 to PP6, the core principles, the do-not-use "
             "list, and the governance and control sections.</p>")

    p.append("<h3>Ready-made prompt templates</h3>"
             "<p>Copy-paste prompts from the addendum itself, each carrying its risk level and "
             "the controls that belong with it. Ask the assistant for one by task "
             "(<em>“give me the prompt template for a BIA interview guide”</em>), or read them "
             'here:</p><ul class="cols">')
    for c in sorted(prompts, key=lambda c: c["breadcrumb"]):
        p.append(f'<li><a href="../kb/{esc(c["id"])}/">{esc(c["breadcrumb"])}</a></li>')
    p.append("</ul>")

    p.append("<h3>What the assistant can do</h3><ul>"
             "<li><strong>Search and cite</strong> the addendum, so every key point carries its "
             "source.</li>"
             "<li><strong>Flag AI risks</strong> before a sensitive task, with the controls that "
             "apply.</li>"
             "<li><strong>Run the guided BIA</strong> one stage at a time, honouring every "
             "approval gate.</li>"
             "<li><strong>Read and write your company files</strong> in the approved "
             "environment, then check each save really matches what you approved.</li>"
             "<li><strong>Referee its own draft</strong> against the approved method before it "
             "asks you to review it.</li></ul>")

    body = "\n".join(p)
    page = (
        '<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Running a BIA with the assistant — a manager's guide — AI Addendum</title>\n"
        '<meta name="description" content="Plain-language guide for BCM managers: running a '
        'Business Impact Analysis with the AI assistant. The five stages, the '
        'five prompts, what to expect, and what only you can catch.">\n'
        f"<style>{STYLE}{EXTRA}</style>\n"
        '<div class="wrap">\n'
        + brand.masthead("guide") + '\n'
        '<p class="crumb"><a href="../kb/">AI Addendum</a> / For BCM managers</p>\n'
        "<h1>Running a BIA with the assistant</h1>\n"
        '<p class="meta">A manager\'s guide <span class="dot">·</span> Teams or your browser '
        '<span class="dot">·</span> <a href="#stages">five stages</a> '
        '<span class="dot">·</span> <a href="#prompts">five prompts</a> '
        '<span class="dot">·</span> <a class="hi" href="#start">demo data only</a></p>\n'
        f"{body}\n"
        + brand.footer_nav("guide") + "\n"
        "<footer>AI Addendum · guidance, not authoritative legal or compliance advice · "
        "AI prepares; people decide, approve and act.</footer>\n"
        "</div>\n"
    )
    out.mkdir(parents=True, exist_ok=True)
    dest = out / "index.html"
    dest.write_text(page, encoding="utf-8")
    return dest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args()
    d = build(a.out)
    print(f"wrote {d} ({d.stat().st_size} bytes)")
