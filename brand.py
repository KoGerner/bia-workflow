"""The estate's one visual system, in one place.

Source of truth is the Ditto style reference (KG, 2026-08-20), and
`public/changelog.html` is its reference implementation — read that file's head comment
before changing anything here.

Why a Python module and not a shared brand.css: the six HTML surfaces on
agent.ai4bcm.org deploy through three different lanes. /changelog and /demo/graph/ are
served straight from the checkout and land with a `git pull`; /demo/kb/, the guide and
the two hand pages are written into the root-owned /var/www/addendum-demo and need a
root round. A stylesheet shared across those lanes desynchronises the moment one lane
runs without the other — the page updates and its stylesheet does not, or the reverse.
Three generators importing one constant cannot desynchronise, because the CSS ships
inside the page it styles.

That leaves three hand-written pages that cannot import: the changelog and the two
deploy/ pages. `tests/test_brand.py` asserts every surface carries a byte-identical
TOKENS block, so drift is a test failure rather than something you notice by eye.

The annotation palette from the reference (Highlight Green #3e6b15, Marker Yellow
#ffdd33, Edit Orange #ff6137, Markup Purple #b26dc2, Comment Blue #0097e6, Sticky Pink
#f5c4cc, Olive Gold #aa7e2e, Mustard #bbb809) is deliberately NOT declared below. The
reference allows it only as marker ink over running text or as small pill stickers, and
the owner removed both from this estate on 2026-08-20. Declaring colours nothing uses
is how they come back as decoration. If ink returns, add it here and nowhere else.
"""

# The neutrals, exactly as the reference names them, plus the type scale. Every value
# below is used by at least one surface; nothing is declared speculatively.
TOKENS = """  /* Ditto neutrals — parchment canvas, India Ink type. No accent hue anywhere: a link
     earns its distinction from an underline, not a colour. */
  --parchment:#f7f5f3; --ink:#000; --charcoal:#222; --graphite:#6a6559;
  --bone:#fff; --smoke:#e2e2e2; --linen:#dcd8cf;
  --sans:Inter,"GT Walsheim",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  /* The reference's type scale, verbatim. Tracking is per size and gets harder as the
     size grows — that negative tracking on display sizes is the system's signature and
     must not be normalised. */
  --t-caption:13px;    --lh-caption:1.43; --ls-caption:.1px;
  --t-body-sm:16px;    --lh-body-sm:1.43; --ls-body-sm:-.16px;
  --t-body:18px;       --lh-body:1.43;    --ls-body:-.22px;
  --t-subheading:26px; --lh-subheading:1.2;  --ls-subheading:-.52px;
  --t-heading-sm:35px; --lh-heading-sm:1.2;  --ls-heading-sm:-.35px;
  --t-heading:43px;    --lh-heading:1.05;    --ls-heading:-.99px;
  --t-heading-lg:72px; --lh-heading-lg:1;    --ls-heading-lg:-2.88px;
  --t-display:108px;   --lh-display:.95;     --ls-display:-4.32px;
  /* Radii, verbatim: pill for buttons/tags/nav, 12px for cards and images, 28px for
     inputs and inputs only — it is the highest non-pill radius in the system. */
  --r-pill:9999px; --r-card:12px; --r-input:28px;
  --shell:1200px"""

# Parchment and ink swap roles. Nothing turns blue. Used only where a surface has a
# reason to follow the OS — today that is the Teams embed and the knowledge pages.
TOKENS_DARK = """  --parchment:#14130f; --ink:#f7f5f3; --charcoal:#d6d2c9; --graphite:#9d968a;
  --bone:#1e1c17; --smoke:#2a2721; --linen:#34302a"""

# The masthead. One row: wordmark left, ghost link, black pill CTA hard right — the
# reference's "every page gets exactly one" filled black CTA. Site pages carry it.
# App surfaces (the sign-in gate, the Teams embed) deliberately do not: a marketing nav
# inside a signed-in workspace is chrome the user cannot act on.
MASTHEAD_CSS = """.top{display:flex;flex-wrap:wrap;align-items:center;gap:20px;
  padding-bottom:28px;border-bottom:1px solid var(--linen)}
.mark{font-weight:700;font-size:var(--t-body-sm);letter-spacing:var(--ls-body-sm);
  margin:0 auto 0 0}
.mark a{color:var(--ink);text-decoration:none}
.ghost{color:var(--ink);font-size:var(--t-body-sm);letter-spacing:var(--ls-body-sm);
  text-decoration:none;border-bottom:1px solid var(--linen)}
.ghost:hover{border-bottom-color:var(--ink)}
.cta{display:inline-block;background:var(--ink);color:var(--bone);border:1px solid var(--ink);
  border-radius:var(--r-pill);padding:8px 15px;font-weight:700;font-size:var(--t-body-sm);
  letter-spacing:var(--ls-body-sm);text-decoration:none}
.cta:hover{background:var(--charcoal);border-color:var(--charcoal)}"""

FOOTER_CSS = """/* The same destinations again at the foot of a long page. Not interconnection for its own
   sake — the guide is 35KB, so a reader who reaches the bottom is eight screens from the top
   nav. Short pages do not need it and app surfaces do not get it.
   Shape-only pills, per the reference: no fill on default, and the current page is marked by
   weight 700 rather than an underline. */
.foot{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 14px}
.foot a,.foot span{font-size:var(--t-body-sm);letter-spacing:var(--ls-body-sm);
  border:1px solid var(--linen);border-radius:var(--r-pill);padding:5px 12px;
  color:var(--ink);text-decoration:none}
.foot a:hover{border-color:var(--ink)}
.foot span{font-weight:700;border-color:var(--ink)}
/* a.cta, not .cta: `.foot a` above is (0,1,1) and beats a bare `.cta` at (0,1,0), so the
   black pill inherited --ink for its text and rendered black-on-black at 1:1. Token-level
   contrast checks cannot see this — they compare declared values, not the cascade. */
.foot a.cta{color:var(--bone);border-color:var(--ink);padding:5px 12px}
.foot a.cta:hover{background:var(--charcoal);border-color:var(--charcoal)}"""

# Every destination a reader can reach. One list, so the masthead and the footer can never
# disagree about what the site contains — which is the failure that made /changelog
# unreachable from 147 pages while every one of them linked to three other places.
_DESTINATIONS = [
    ("kb", "/demo/kb/", "Knowledge base"),
    ("guide", "/demo/bia-workflow-guide/", "Manager&rsquo;s guide"),
    ("changelog", "/changelog", "Release notes"),
]
_CTA = ("/demo/bia-live/", "Try the demo")


def root_css() -> str:
    """`:root` plus the dark override, ready to prepend to any page's own rules.

    Composed, never pasted: `build_kb_pages.STYLE` used to carry a literal copy of TOKENS,
    which meant a change here reached the three hand-written pages (the drift test checks
    them) and silently missed all 147 generated ones. tests/test_brand.py now asserts the
    generator does NOT contain TOKENS as source text and DOES emit it in a built page.
    """
    return (":root{\n" + TOKENS + ";\n}\n"
            "@media(prefers-color-scheme:dark){:root{\n" + TOKENS_DARK + "}}\n")


def masthead(here: str = "") -> str:
    """The top nav row. `here` names the current page so it does not link to itself."""
    mark = ('<p class="mark">BIA Workflow</p>' if here == "kb"
            else '<p class="mark"><a href="/demo/kb/">BIA Workflow</a></p>')
    links = "".join(f'\n    <a class="ghost" href="{href}">{label}</a>'
                    for key, href, label in _DESTINATIONS
                    if key != here and key != "kb")
    return (f'  <div class="top">\n    {mark}{links}\n'
            f'    <a class="cta" href="{_CTA[0]}">{_CTA[1]}</a>\n  </div>')


def footer_nav(here: str = "") -> str:
    """The same destinations at the foot of a long page. The current one is not a link."""
    items = "".join(
        f'\n    <span>{label}</span>' if key == here else f'\n    <a href="{href}">{label}</a>'
        for key, href, label in _DESTINATIONS)
    return (f'  <nav class="foot" aria-label="Site">{items}\n'
            f'    <a class="cta" href="{_CTA[0]}">{_CTA[1]}</a>\n  </nav>')
