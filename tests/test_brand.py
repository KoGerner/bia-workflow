"""Every surface on agent.ai4bcm.org carries the same design tokens.

Three deploy lanes serve these six pages, so a shared stylesheet would desynchronise
(see brand.py). The generators import brand.TOKENS and cannot drift; the three
hand-written pages carry it inline and can. This is the check that catches them.
"""
from pathlib import Path

import brand

APP = Path(__file__).resolve().parent.parent
# public/changelog.html was the third of these until it was retired on 2026-08-25. status.html
# did NOT take its place: it carries a status palette (including #3e6b15 on fills) and no
# verbatim TOKENS block, so it is hand-written but not a token surface.
HAND_WRITTEN = [
    APP / "deploy" / "bia-live-gate.html",
    APP / "deploy" / "bia-live-embed.html",
]


def test_hand_written_pages_carry_the_shared_tokens():
    for page in HAND_WRITTEN:
        assert brand.TOKENS in page.read_text(encoding="utf-8"), (
            f"{page.name} has drifted from brand.TOKENS — re-splice it rather than "
            f"editing the block in place"
        )


def test_no_page_reintroduces_an_annotation_colour():
    # The reference allows the annotation palette only as marker ink over running text,
    # and the owner removed that on 2026-08-20. A hex from it in a page means it came
    # back as decoration.
    banned = ["#3e6b15", "#ffdd33", "#ff6137", "#b26dc2", "#0097e6", "#f5c4cc", "#bbb809"]
    sources = HAND_WRITTEN + [APP / "build_kb_pages.py", APP / "build_guide_page.py"]
    for src in sources:
        text = src.read_text(encoding="utf-8").lower()
        for hexcode in banned:
            head = text.split("<style>")[0] if "<style>" in text else ""
            assert hexcode not in text.replace(head, "", 1), f"{src.name} reintroduces {hexcode}"


def test_the_type_scale_matches_the_reference():
    # The reference's scale, verbatim. If a size or its tracking changes here it changed
    # in the Ditto reference, and that is a decision, not a tidy-up.
    for size, tracking in [("13px", ".1px"), ("16px", "-.16px"), ("18px", "-.22px"),
                           ("26px", "-.52px"), ("35px", "-.35px"), ("43px", "-.99px"),
                           ("72px", "-2.88px"), ("108px", "-4.32px")]:
        assert size in brand.TOKENS and tracking in brand.TOKENS


def test_the_generators_compose_the_system_rather_than_copying_it():
    """A pasted copy is not consolidation — it just moves the drift somewhere quieter.

    build_kb_pages.STYLE carried a literal copy of brand.TOKENS until 2026-08-20, so a change
    in brand.py reached the three hand-written pages (the test above) and silently missed all
    147 generated ones, while every check still passed. The generator must therefore NOT
    contain the token block as source text, and MUST emit it in the composed stylesheet.
    """
    import build_kb_pages

    source = (APP / "build_kb_pages.py").read_text(encoding="utf-8")
    assert brand.TOKENS not in source, "build_kb_pages.py has a pasted copy of brand.TOKENS"
    assert brand.TOKENS in build_kb_pages.STYLE
    assert brand.MASTHEAD_CSS in build_kb_pages.STYLE
    assert brand.FOOTER_CSS in build_kb_pages.STYLE


def test_nothing_is_an_orphan_or_a_dead_end():
    """Every page reaches the others, and every destination is reachable.

    Measured 2026-08-20 before this landed: /changelog had zero inbound links from 147 pages,
    and the graph page had zero links in either direction. Both were invisible to every check
    that only looked at one page at a time.
    """
    import re as _re

    gate = (APP / "deploy" / "bia-live-gate.html").read_text(encoding="utf-8")
    embed = (APP / "deploy" / "bia-live-embed.html").read_text(encoding="utf-8")
    graph_src = (APP / "dep_graph.py").read_text(encoding="utf-8")

    # /changelog was retired 2026-08-25 and its nginx location 301s to GitHub Releases, so it
    # is no longer a destination. The invariant is not about that page — it is that whatever
    # IS in the list stays reachable and no page is a dead end.
    assert not any(href == "/changelog" for _, href, _ in brand._DESTINATIONS)
    assert brand._DESTINATIONS, "an empty destination list makes every page a dead end"
    # …and every destination that IS listed is built by something in this tree. The nginx
    # /demo/ location is a catch-all, so it cannot tell a live path from a typo; the builders
    # can. An independent review on 2026-08-25 pointed out that the check above passes with
    # _DESTINATIONS aimed at /demo/does-not-exist/ — non-empty is not reachable.
    built = set()
    for mod in ("build_kb_pages.py", "build_guide_page.py"):
        for out in _re.findall(r'DEFAULT_OUT = Path\("([^"]+)"\)',
                               (APP / mod).read_text(encoding="utf-8")):
            built.add(out.rstrip("/").rsplit("/", 1)[-1])
    for _key, href, _label in brand._DESTINATIONS:
        leaf = href.strip("/").rsplit("/", 1)[-1]
        assert leaf in built, f"{href} is in the shared nav but no builder here produces it"
    # No page is a dead end.
    for name, page in (("gate", gate), ("embed", embed)):
        assert _re.search(r'href="/demo', page), f"{name} has no outbound link"
    # The graph page is unlisted but not stranded.
    assert '<p class="home"><a href="/demo/kb/">' in graph_src
    # …and it is reachable from inside the signed-in workspace, not from public nav.
    assert 'href="/demo/graph/marschkamp/"' in embed
    assert not any("graph" in href for _, href, _ in brand._DESTINATIONS)


def test_the_footer_cta_is_not_black_on_black():
    """The cascade, not the token, is what a reader sees.

    `.foot a` sets color:var(--ink) at specificity (0,1,1); a bare `.cta` sets
    color:var(--bone) at (0,1,0) and loses. The black pill rendered black text on a black
    fill at 1:1 and shipped live, because every contrast check this session compared
    declared token values rather than resolving which rule wins.
    """
    import re as _re

    assert ".foot a.cta{" in brand.FOOTER_CSS, "the CTA override must out-specify `.foot a`"
    rule = _re.search(r"\.foot a\.cta\{([^}]*)\}", brand.FOOTER_CSS).group(1)
    assert "color:var(--bone)" in rule, "footer CTA text must be Bone White on the ink pill"
