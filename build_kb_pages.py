#!/usr/bin/env python3
"""Emit one static HTML page per knowledge chunk so citation URLs actually resolve.

Reads data/chunks.json (built by build_chunks.py) and writes
/var/www/addendum-demo/kb/<id>/index.html plus a kb/index.html listing. Served by
the existing nginx /demo/ block: no sudo, no service restart, idempotent.
Run automatically as step 1b of publish_knowledge.sh.

Chunk text is Markdown. It is escaped first, then marked up, so the pages stay
XSS-safe while bold/lists/quotes actually render. Only the constructs the corpus
uses are supported (bold, italic, inline code, ul, ol, blockquote, continuation
lines) — headings and md links appear in 0/146 chunks, so they are not parsed.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
from pathlib import Path

import brand

DEFAULT_CHUNKS = Path(os.environ.get("BIA_WORKFLOW_DATA_DIR",
                                     Path(__file__).resolve().parent / "data")) / "chunks.json"
DEFAULT_OUT = Path("/var/www/addendum-demo/kb")
PUBLIC_BASE_URL = "https://agent.ai4bcm.org/demo/kb"

# The visual system is COMPOSED from brand.py at import time, never pasted: the tokens, the
# masthead and the footer nav all arrive by reference, so a change in brand.py reaches these
# 147 pages and the three hand-written ones together. The aliases below map this file's own
# long-standing names onto the primitives instead of renaming sixty rules — a CSS custom
# property resolves at use time, so --paper follows --parchment into dark mode by itself.
# There is no accent hue left: --accent is India Ink, and a link reads as a link from its
# underline.
_ALIASES = """:root{--paper:var(--parchment);--ink-2:var(--charcoal);--ink-3:var(--graphite);
--rule:var(--linen);--tint:var(--bone);--accent:var(--ink);--accent-2:var(--charcoal);
--flag:var(--ink);--display:var(--sans);--serif:var(--sans)}
@media(prefers-color-scheme:dark){:root{--accent-2:#fff}}
"""

_RULES = """*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
font:400 var(--t-body)/1.7 var(--sans);letter-spacing:var(--ls-body);
-webkit-text-size-adjust:100%}
.wrap{max-width:41rem;margin:0 auto;padding:3rem 1.25rem 4rem}
.crumb{font:var(--t-caption)/var(--lh-caption) var(--sans);letter-spacing:var(--ls-caption);
text-transform:uppercase;
color:var(--ink-2);margin:0 0 1.2rem}
.crumb a{color:var(--ink-2);text-decoration:none}
.crumb a:hover{color:var(--accent)}
/* heading-sm -> heading on the reference scale; the tracking hardens with the size, which is
   the signature. Do not normalise it. */
h1{font-family:var(--sans);font-weight:700;
font-size:clamp(var(--t-heading-sm),1.25rem + 3vw,var(--t-heading));
line-height:var(--lh-heading);letter-spacing:var(--ls-heading);margin:0 0 1.5rem}
.meta{display:flex;flex-wrap:wrap;align-items:baseline;gap:.3rem .55rem;
font:var(--t-caption)/var(--lh-caption) var(--sans);letter-spacing:var(--ls-caption);
text-transform:uppercase;color:var(--ink-3);border-top:1px solid var(--rule);border-bottom:1px solid var(--rule);
padding:.7rem 0;margin:0 0 2.3rem}
.meta .dot{color:var(--rule);user-select:none}
.meta a{color:var(--ink-2);text-decoration:none;
border-bottom:1px dotted var(--ink-3);padding-bottom:.05rem}
.meta a:hover{color:var(--accent);border-bottom-color:var(--accent)}
.meta a.pp{color:var(--paper);background:var(--accent);border:0;border-radius:var(--r-pill);
padding:.22rem .62rem;font-weight:700}
.meta a.pp:hover{background:var(--accent-2);color:var(--paper)}
.meta a.hi{color:var(--flag);border-bottom-color:var(--flag)}
p{margin:0 0 1.15rem}
.lede{font-size:var(--t-subheading);line-height:var(--lh-subheading);
letter-spacing:var(--ls-subheading);color:var(--ink-2)}
strong{font-weight:700;color:var(--ink)}
code{font:.88em var(--mono);background:var(--tint);border:1px solid var(--rule);
border-radius:6px;padding:.1em .4em}
ul,ol{margin:0 0 1.15rem;padding:0;list-style:none}
li{position:relative;padding-left:2.1rem;margin:0 0 .8rem}
ul li::before{content:"";position:absolute;left:.55rem;top:.8em;width:.45rem;height:1px;
background:var(--ink-3)}
ol li::before{content:counter(list-item) ".";position:absolute;left:0;top:.02em;
font:700 .84rem/2 var(--sans);font-variant-numeric:tabular-nums;color:var(--accent)}
.cont{margin:.55rem 0 0;color:var(--ink-2)}
blockquote{margin:0 0 1.15rem;padding:.15rem 0 .15rem 1.1rem;border-left:2px solid var(--accent);
color:var(--ink-2)}
blockquote p:last-child{margin:0}
a{color:var(--accent);text-underline-offset:.18em;text-decoration-thickness:1px}
a:hover{color:var(--accent-2)}
:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:6px}
footer{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--rule);
font-size:var(--t-caption);line-height:var(--lh-caption);letter-spacing:var(--ls-caption);
color:var(--ink-3)}
.cite{font:var(--t-caption)/1.7 var(--mono);color:var(--ink-3);word-break:break-all;margin:.5rem 0 0}
.grp{margin:2.6rem 0 0}
.grp>h2{font:var(--t-caption)/var(--lh-caption) var(--sans);letter-spacing:var(--ls-caption);
text-transform:uppercase;
color:var(--ink-2);margin:0;padding-bottom:.55rem;border-bottom:2px solid var(--ink)}
.grp h3{font:700 var(--t-subheading)/var(--lh-subheading) var(--sans);
letter-spacing:var(--ls-subheading);color:var(--ink);
margin:1.3rem 0 .5rem}
.grp ul{margin:.6rem 0 0}
.grp ul li{padding-left:0;margin:0 0 .45rem}
.grp ul li::before{display:none}
.grp a{text-decoration:none}
.grp a:hover{text-decoration:underline}
"""

STYLE = brand.root_css() + _ALIASES + brand.MASTHEAD_CSS + "\n" + brand.FOOTER_CSS + "\n" + _RULES


PAGE = (
    '<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    "<title>{title} — BIA-Workflow</title>\n"
    '<meta name="description" content="{desc}">\n'
    "<style>{style}</style>\n"
    '<div class="wrap">\n'
    + brand.masthead("") + '\n'
    '<p class="crumb"><a href="../">Knowledge base</a> / {breadcrumb}</p>\n'
    "<h1>{title}</h1>\n{rail}{body}\n"
    + brand.footer_nav("") + '\n'
    "<footer>BIA-Workflow knowledge base · <a href=\"../\">all sections</a> · "
    "guidance, not authoritative legal or compliance advice"
    '<p class="cite">Cited as {url}</p></footer>\n'
    "</div>\n"
)

INDEX = (
    '<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    "<title>BIA-Workflow — knowledge base</title>\n<style>{style}</style>\n"
    '<div class="wrap">\n' + brand.masthead("kb") + '\n'
    "<h1>Knowledge base</h1>\n"
    '<p class="lede">{count} sections, cited by the BIA-Workflow assistant. '
    "Each page is the source behind a citation link.</p>\n{groups}\n"
    + brand.footer_nav("kb") + '\n'
    "<footer>guidance, not authoritative legal or compliance advice</footer>\n</div>\n"
)

FACET_PAGE = (
    '<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    "<title>{label} — BIA-Workflow</title>\n<style>{style}</style>\n"
    '<div class="wrap">\n'
    + brand.masthead("") + '\n'
    '<p class="crumb"><a href="../../">Knowledge base</a> / {label}</p>\n'
    "<h1>{label}</h1>\n"
    '<p class="lede">{count} section{plural} tagged this way.</p>\n'
    "<ul>\n{items}\n</ul>\n"
    + brand.footer_nav("") + '\n'
    "<footer>BIA-Workflow knowledge base · <a href=\"../../\">all sections</a> · "
    "guidance, not authoritative legal or compliance advice</footer>\n</div>\n"
)

PP_LABEL = {
    "all": "Across the addendum", "pp1": "PP1 — Policy & programme management",
    "pp2": "PP2 — Embedding", "pp3": "PP3 — Analysis", "pp4": "PP4 — Design",
    "pp5": "PP5 — Implementation", "pp6": "PP6 — Validation",
}


def _inline(s: str) -> str:
    """Escape first, then mark up — so injected tags are only ever ours."""
    s = html.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])", r"<em>\1</em>", s)
    return s


def render_markdown(text: str, title: str = "", breadcrumb: str = "") -> str:
    """Block-level Markdown → HTML, limited to what the corpus actually contains.

    Line structure is read from the raw text (so a leading `>` is still a quote),
    and only the inner content is escaped. A first line repeating the page title or
    the breadcrumb is dropped — the <h1> and crumb already say it.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    known = {s.strip() for s in (title, breadcrumb) if s.strip()}
    if lines and lines[0].strip() in known:
        lines.pop(0)

    out: list[str] = []
    items: list[list[str]] = []
    kind = ""          # "ul" | "ol" | "" when no list is open
    start = 1          # source number of the first item, so a split list resumes
    quote: list[str] = []
    first_para = True

    def close_list() -> None:
        nonlocal kind, items
        if kind:
            body = "".join(f"<li>{''.join(frag)}</li>" for frag in items)
            attr = f' start="{start}"' if kind == "ol" and start != 1 else ""
            out.append(f"<{kind}{attr}>{body}</{kind}>")
        kind, items = "", []

    def close_quote() -> None:
        if quote:
            out.append("<blockquote>" + "".join(f"<p>{q}</p>" for q in quote) + "</blockquote>")
            quote.clear()

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            close_list()
            close_quote()
            continue

        m_ol = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        m_ul = re.match(r"^[-*+]\s+(.*)$", stripped)
        m_q = re.match(r"^>\s?(.*)$", stripped)

        if m_q:
            close_list()
            quote.append(_inline(m_q.group(1)))
            continue
        close_quote()

        if m_ol or m_ul:
            want = "ol" if m_ol else "ul"
            if kind != want:
                close_list()
                kind = want
                if m_ol:
                    start = int(m_ol.group(1))
            items.append([_inline((m_ol or m_ul).group(2 if m_ol else 1))])
            continue

        # An indented line under an open list continues that item.
        if kind and items and raw[:1].isspace():
            items[-1].append(f'<p class="cont">{_inline(stripped)}</p>')
            continue

        close_list()
        cls = ' class="lede"' if first_para else ""
        out.append(f"<p{cls}>{_inline(stripped)}</p>")
        first_para = False

    close_list()
    close_quote()
    return "\n".join(out)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _facets(c: dict) -> list[tuple[str, str, str]]:
    """(slug-path, display-label, css-class) per facet of a chunk. One function feeds
    both the rail links and the generated facet pages, so hrefs can never drift from
    the pages that satisfy them. confidentiality is deliberately omitted: every kb
    page is public, and its "high" renders indistinguishably from risk "high"."""
    out: list[tuple[str, str, str]] = []
    for prefix, field in (("type", "section_type"), ("output", "output_type")):
        val = (c.get(field) or "").strip()
        if val:
            label = val.replace("_", " ")
            out.append((f"{prefix}-{_slug(val)}", label, ""))
    risk = (c.get("risk_level") or "").strip()
    if risk:
        out.append((f"risk-{_slug(risk)}", f"risk {risk}",
                    "hi" if risk.lower() == "high" else ""))
    for user in (c.get("intended_user") or "").split(","):
        user = user.strip()
        if user:
            out.append((f"for-{_slug(user)}", user, ""))
    return out


def _rail(c: dict) -> str:
    """Provenance strip built only from metadata already in chunks.json. Every item
    is a working link: PP jumps to its index group, the rest open facet pages."""

    parts: list[str] = []
    pp = (c.get("pp") or "").strip()
    if pp:
        parts.append(f'<a class="pp" href="../#{html.escape(pp)}">{html.escape(pp.upper())}</a>')
    for slug, label, css in _facets(c):
        cls = f' class="{css}"' if css else ""
        parts.append(f'<a{cls} href="../t/{html.escape(slug)}/">{html.escape(label)}</a>')
    if not parts:
        return ""
    joined = '<span class="dot" aria-hidden="true">·</span>'.join(parts)
    return f'<p class="meta">{joined}</p>\n'


def build(chunks_path: Path = DEFAULT_CHUNKS, out_dir: Path = DEFAULT_OUT) -> int:
    data = json.loads(chunks_path.read_text(encoding="utf-8"))
    chunks = data if isinstance(data, list) else data["chunks"]
    out_dir.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list[str]] = {}
    facets: dict[str, tuple[str, list[tuple[str, str]]]] = {}
    for c in chunks:
        page_dir = out_dir / c["id"]
        page_dir.mkdir(parents=True, exist_ok=True)
        text = c.get("text", "")
        desc = " ".join(re.sub(r"[*`>#]", "", text).split())[:180]
        (page_dir / "index.html").write_text(
            PAGE.format(style=STYLE, title=html.escape(c["title"]),
                        breadcrumb=html.escape(c["breadcrumb"]),
                        desc=html.escape(desc, quote=True),
                        url=f'{PUBLIC_BASE_URL}/{html.escape(c["id"])}/',
                        rail=_rail(c),
                        body=render_markdown(text, c["title"], c["breadcrumb"])),
            encoding="utf-8")
        groups.setdefault((c.get("pp") or "all").strip() or "all", []).append(
            (c["breadcrumb"], c["id"]))
        for slug, label, _css in _facets(c):
            facets.setdefault(slug, (label, []))[1].append((c["breadcrumb"], c["id"]))
    for slug, (label, members) in facets.items():
        page_dir = out_dir / "t" / slug
        page_dir.mkdir(parents=True, exist_ok=True)
        items = "\n".join(f'<li><a href="../../{html.escape(cid)}/">'
                          f"{html.escape(crumb)}</a></li>" for crumb, cid in members)
        (page_dir / "index.html").write_text(
            FACET_PAGE.format(style=STYLE, label=html.escape(label),
                              count=len(members),
                              plural="" if len(members) == 1 else "s", items=items),
            encoding="utf-8")
    blocks = []
    for key in sorted(groups, key=lambda k: (k == "all", k)):
        label = PP_LABEL.get(key, key.upper())
        # Rows sharing a document prefix ("Doc title > Section") get one serif
        # subheading and short section links, instead of repeating the prefix per row.
        rows: list[str] = []
        open_ul = False
        last_prefix: str | None = None
        for crumb, cid in groups[key]:
            prefix, _, leaf = crumb.partition(" > ")
            prefix = prefix if leaf else ""
            text = leaf or crumb
            if prefix != last_prefix:
                if open_ul:
                    rows.append("</ul>")
                    open_ul = False
                if prefix:
                    rows.append(f"<h3>{html.escape(prefix)}</h3>")
                last_prefix = prefix
            if not open_ul:
                rows.append("<ul>")
                open_ul = True
            rows.append(f'<li><a href="{html.escape(cid)}/">{html.escape(text)}</a></li>')
        if open_ul:
            rows.append("</ul>")
        blocks.append(f'<section class="grp" id="{html.escape(key)}">'
                      f"<h2>{html.escape(label)}</h2>\n" + "\n".join(rows) + "</section>")
    (out_dir / "index.html").write_text(
        INDEX.format(style=STYLE, count=len(chunks), groups="\n".join(blocks)),
        encoding="utf-8")
    return len(chunks)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    n = build(args.chunks, args.out)
    print(f"OK — {n} kb pages -> {args.out}")
