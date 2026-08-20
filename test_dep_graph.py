"""Tests for dep_graph — pure derivation, layout, and rendering. No network anywhere."""
import json

import re

import dep_graph


REG = {
    "synthetic": True,
    "KA-01": {"asset_id": "KA-01", "name": "cooling plant", "asset_type": "technical",
              "bereich": "infrastruktur", "owner_name": "R. Boll", "criticality": "critical",
              "rto": "4 h", "rpo": "n/a", "mtpd": "8 h", "spof": True, "redundancy": "none",
              "supplier": "Kältetechnik GmbH", "contract_status": "in force",
              "depends_on": ["UV-STROM-01"], "pp4_issue": True,
              "quality_flags": ["single compressor"],
              "consumers": [
                  {"dept": "zerlegung", "activity": "chilled cutting", "need": "≤ 12 °C",
                   "consumer_mtpd": "≈ 2-4 h"},
                  {"dept": "qs", "activity": "HACCP evidence", "need": "trend data"}]},
    "UV-STROM-01": {"asset_id": "UV-STROM-01", "name": "power supply", "asset_type": "utility",
                    "bereich": "infrastruktur", "owner_name": None, "criticality": "critical",
                    "rto": "2 h", "rpo": "n/a", "mtpd": "4 h", "spof": True, "redundancy": "none",
                    "supplier": "Stadtwerke", "contract_status": "in force",
                    "depends_on": [], "pp4_issue": False, "quality_flags": [], "consumers": []},
}

# Two assets that depend on each other — the smallest register that exercises the SCC
# column collapse, both chain walks, and a focus keep-set that covers the whole graph.
CYCLE = {
    "A-01": {"asset_id": "A-01", "name": "a", "depends_on": ["B-01"], "consumers": []},
    "B-01": {"asset_id": "B-01", "name": "b", "depends_on": ["A-01"], "consumers": []},
}

RECORD = {"activities": [
    {"id": "act-1", "name": "packing line", "owner_name": "P. Louven",
     "recovery_target": "8 h", "mtpd": "24 h",
     "dependencies": ["KA-01", {"id": "NOPE-99"}]},
]}

# The shape the one real saved record (marschkamp, 2026-07-30) actually has: owner not
# owner_name, no id, no dependencies — bia_referee validates exactly this vocabulary.
REAL_SHAPE_RECORD = {"activities": [
    {"name": "Slaughter Process", "owner": "Torsten Ahlgrim",
     "recovery_target": "8 h", "mtpd": "8 h"},
]}


def _node(graph, node_id):
    return next(n for n in graph["nodes"] if n["id"] == node_id)


def test_build_graph_assets_and_depends_on_edges():
    g = dep_graph.build_graph(REG)
    assert _node(g, "KA-01")["kind"] == "asset"
    assert _node(g, "KA-01")["quality_flag_count"] == 1
    assert {"src": "KA-01", "dst": "UV-STROM-01", "kind": "depends_on"} in g["edges"]


def test_build_graph_owner_missing_derived_from_falsy_owner():
    g = dep_graph.build_graph(REG)
    assert _node(g, "UV-STROM-01")["owner_missing"] is True
    assert _node(g, "KA-01")["owner_missing"] is False


def test_build_graph_consumer_process_nodes_and_edges():
    g = dep_graph.build_graph(REG)
    proc = _node(g, "proc:zerlegung:chilled cutting")
    assert proc["kind"] == "process" and proc["consumer_mtpd"] == "≈ 2-4 h"
    assert {"src": "KA-01", "dst": "proc:zerlegung:chilled cutting",
            "kind": "consumes"} in g["edges"]


def test_build_graph_ignores_non_dict_register_entries():
    g = dep_graph.build_graph(REG)  # "synthetic": True must not crash or become a node
    assert all(n["id"] != "synthetic" for n in g["nodes"])


def test_build_graph_record_overlay_and_unmodeled_dep():
    g = dep_graph.build_graph(REG, RECORD)
    act = _node(g, "act:act-1")
    assert act["kind"] == "activity" and act["recovery_target"] == "8 h"
    assert {"src": "act:act-1", "dst": "KA-01", "kind": "activity_dep"} in g["edges"]
    assert _node(g, "NOPE-99")["kind"] == "unmodeled"
    assert {"src": "act:act-1", "dst": "NOPE-99", "kind": "activity_dep"} in g["edges"]


def test_build_graph_activity_with_real_record_shape():
    """A record activity carrying owner/name but no id, owner_name, or dependencies must
    not degrade into the act:? stub with OWNER MISSING — the builder tolerates both
    vocabularies, the same way _crit_class does for criticality."""
    g = dep_graph.build_graph(REG, REAL_SHAPE_RECORD)
    act = next(n for n in g["nodes"] if n["kind"] == "activity")
    assert act["id"] == "act:Slaughter Process"  # derived from the name, never "act:?"
    assert act["owner_name"] == "Torsten Ahlgrim"


def test_dangling_register_dep_becomes_unmodeled_node():
    """D7: register-side depends_on targets that aren't real assets must become visible
    unmodeled nodes too (record-side ones already do; register-side didn't)."""
    reg = {"A-01": {"asset_id": "A-01", "depends_on": ["GHOST-01"], "consumers": []}}
    g = dep_graph.build_graph(reg)
    assert _node(g, "GHOST-01")["kind"] == "unmodeled"
    assert {"src": "A-01", "dst": "GHOST-01", "kind": "depends_on"} in g["edges"]


def test_annotate_activity_chain_walks_register_upstream():
    """2026-07-30 bundle receipt: a record `dependencies` list gives the activity a real
    upstream chain in the facts panel — direct register deps first, then their own
    providers transitively. Records without the field keep the honest empty chain."""
    g = dep_graph._annotate(dep_graph.layout(dep_graph.build_graph(REG, RECORD)))
    act = _node(g, "act:act-1")
    assert act["chain"][0] == "KA-01"
    assert "UV-STROM-01" in act["chain"]  # KA-01's own provider, walked transitively
    g2 = dep_graph._annotate(dep_graph.layout(dep_graph.build_graph(REG, REAL_SHAPE_RECORD)))
    assert _node(g2, "act:Slaughter Process")["chain"] == []


def test_upstream_chain_transitive_with_depth():
    g = dep_graph.build_graph(REG)
    chain = dep_graph.upstream_chain(g, "KA-01")
    assert chain == [{"id": "UV-STROM-01", "depth": 1, "spof": True, "owner_missing": True}]


def test_upstream_chain_survives_cycles():
    g = dep_graph.build_graph(CYCLE)
    chain = dep_graph.upstream_chain(g, "A-01")
    assert [c["id"] for c in chain] == ["B-01"]  # visits each node once, never hangs


def test_downstream_chain_transitive_with_depth():
    g = dep_graph.build_graph(REG)
    chain = dep_graph.downstream_chain(g, "UV-STROM-01")
    # KA-01 depends on UV-STROM-01, so it is UV-STROM-01's downstream dependent — the
    # entry carries KA-01's own spof/owner_missing (KA-01 has an owner, so False here).
    assert {"id": "KA-01", "depth": 1, "spof": True, "owner_missing": False} in chain


def test_downstream_chain_survives_cycles():
    g = dep_graph.build_graph(CYCLE)
    chain = dep_graph.downstream_chain(g, "A-01")
    assert [c["id"] for c in chain] == ["B-01"]  # visits each node once, never hangs


def test_layout_providers_left_of_dependents():
    g = dep_graph.layout(dep_graph.build_graph(REG))
    assert _node(g, "UV-STROM-01")["col"] < _node(g, "KA-01")["col"]


def test_layout_processes_rightmost_and_deterministic():
    g1 = dep_graph.layout(dep_graph.build_graph(REG, RECORD))
    g2 = dep_graph.layout(dep_graph.build_graph(REG, RECORD))
    proc_col = _node(g1, "proc:qs:HACCP evidence")["col"]
    assert proc_col > _node(g1, "act:act-1")["col"] > _node(g1, "KA-01")["col"]
    assert [(n["id"], n["col"], n["row"]) for n in g1["nodes"]] == \
           [(n["id"], n["col"], n["row"]) for n in g2["nodes"]]


def test_layout_cycle_members_share_column_and_terminate():
    g = dep_graph.layout(dep_graph.build_graph(CYCLE))
    assert _node(g, "A-01")["col"] == _node(g, "B-01")["col"]


def test_annotate_gives_processes_a_chain_and_assets_impact():
    g = dep_graph._annotate(dep_graph.layout(dep_graph.build_graph(REG)))
    assert _node(g, "proc:zerlegung:chilled cutting")["chain"][0] == "KA-01"
    assert "UV-STROM-01" in _node(g, "proc:zerlegung:chilled cutting")["chain"]
    assert _node(g, "UV-STROM-01")["impact"] == ["KA-01"]
    assert _node(g, "KA-01")["crit"] == "critical"


def test_annotate_gives_assets_direct_procs_and_acts():
    g = dep_graph._annotate(dep_graph.layout(dep_graph.build_graph(REG, RECORD)))
    assert _node(g, "KA-01")["procs"] == ["proc:zerlegung:chilled cutting",
                                          "proc:qs:HACCP evidence"]
    assert _node(g, "KA-01")["acts"] == ["act:act-1"]
    assert _node(g, "UV-STROM-01")["procs"] == []
    assert _node(g, "UV-STROM-01")["acts"] == []


def test_annotate_gives_process_feeds_and_unmodeled_impact_and_acts():
    g = dep_graph._annotate(dep_graph.layout(dep_graph.build_graph(REG, RECORD)))
    assert _node(g, "proc:zerlegung:chilled cutting")["feeds"] == ["KA-01"]
    unmodeled = _node(g, "NOPE-99")  # record-side unmodeled: reached only via activity_dep
    assert unmodeled["impact"] == []
    assert unmodeled["acts"] == ["act:act-1"]


def test_annotate_gives_dangling_register_dep_populated_impact():
    reg = {"A-01": {"asset_id": "A-01", "depends_on": ["GHOST-01"], "consumers": []}}
    g = dep_graph._annotate(dep_graph.layout(dep_graph.build_graph(reg)))
    assert _node(g, "GHOST-01")["impact"] == ["A-01"]  # register-side unmodeled: reached
    assert _node(g, "GHOST-01")["acts"] == []           # via depends_on, not activity_dep


def _page(record=None, evidence=None):
    return dep_graph.render_page(dep_graph.layout(dep_graph.build_graph(REG, record)),
                                 "marschkamp", evidence)


def _above_fold(html: str) -> str:
    """Everything rendered between the header and the closed provenance fold, stripped.

    Asserting on the region rather than on a container's id is what survives a rename: a
    negative pin like `'id="second-opinion"' not in html` goes trivially true the moment the
    emitter calls itself something else."""
    return html.split("</header>")[1].split('<details class="provenance">')[0].strip()


def test_render_page_captions_data_classification_from_the_register():
    """The caption used to be hardcoded, so a real company's page would have printed
    "synthetic demonstration data" over live data. It is now read off the register's own
    marker, and an unmarked register makes no claim at all."""
    assert "synthetic demonstration data" in _page()  # REG carries "synthetic": True

    real = {k: v for k, v in REG.items() if k != "synthetic"}
    page = dep_graph.render_page(dep_graph.layout(dep_graph.build_graph(real)), "realco")
    assert "synthetic demonstration data" not in page  # the folded provenance line
    assert "Synthetic demonstration data" not in page  # and the headline that leads the page
    # An unmarked register still gets its headline, uncaptioned. Since 2026-08-04 that
    # headline dates the DATA, so with nothing banked it says so rather than borrowing
    # the render clock.
    assert "read live at generation" in page
    assert "Generated " in page  # the rest of the meta line survives, folded into provenance


def test_header_leads_human_and_folds_provenance():
    """KG 2026-07-31: one compact human line up top; timestamps, shas and the
    evidence rows live one click away in a native <details>. The synthetic
    caption still never appears on a non-synthetic page."""
    html = _page(RECORD)  # REG fixture is synthetic
    # The caption still leads; what follows it is the data's age, not the render clock.
    assert "Synthetic demonstration data · read live at generation" in html
    assert '<details class="provenance">' in html
    # containment, not just ordering: a </details> that closed early would keep every
    # "opens before" true and still leave the evidence rows unfolded on the page
    assert html.index('<details class="provenance">') < html.index("Generated 20")
    assert html.index('<details class="provenance">') < html.index('id="evidence"')
    assert html.index('id="evidence"') < html.index("</details>")
    # the band's own chrome, not just its cursor: without these three the fold renders
    # full-bleed on the body background at body size — what <header> used to prevent
    assert "details.provenance { background: #ffffff; border-bottom: 1px solid #e2e8f0; }" \
        in dep_graph._CSS
    assert "details.provenance summary { cursor: pointer; margin-top: 0; }" in dep_graph._CSS
    assert "details.provenance > .meta { padding: 2px 20px; }" in dep_graph._CSS


def test_nothing_renders_between_header_and_fold():
    """The compact header is the branch's whole point: the page must run headline → fold
    with no container between them (the second-opinion row that once lived there is gone)."""
    html = _page(RECORD, {"register": {"sha": "a3b8f7cc", "human_line": "✓ Saved."},
                          "record": None})
    assert _above_fold(html) == ""
    assert _above_fold(_page()) == ""  # and with no evidence argument at all


def test_legend_and_footer_note_fold_into_provenance():
    """KG 2026-07-31: the legend and the closing explainer ate space above and below the
    canvas on every load, on a page whose whole point is showing as much graph as possible
    on screen. Both are static prose nobody needs to re-read after the first visit, so both
    fold into the drawer that already exists for exactly that — Provenance — collapsed by
    default, one click away, matching how the ⚠ warning / clean-evidence split already
    treats that fold as "the place explanatory text goes when it isn't a live finding"."""
    html = _page(RECORD)
    fold_start = html.index('<details class="provenance">')
    fold_end = html.index("</details>")
    # both blocks of text now live strictly inside the fold...
    assert fold_start < html.index("no recorded upstream dependencies") < fold_end
    assert fold_start < html.index("Register-derived view") < fold_end
    # ...not duplicated outside it
    assert html.count("no recorded upstream dependencies") == 1
    assert html.count("Register-derived view") == 1
    # the legend keeps its own class/styling, just relocated
    assert '<div class="legend">' in html
    # the standalone <footer> element is gone; its text is a `.meta` line inside the fold,
    # matching the "Generated ..." line's presentation rather than keeping a whole element
    # and its own CSS rule alive for one sentence
    assert "<footer>" not in html
    # nothing but whitespace between the fold and the toolbar, or between </main> and the
    # scripts — if either block were still there too, it duplicated instead of moved
    assert html.split("</details>")[1].split('<div id="toolbar">')[0].strip() == ""
    assert html.split("</main>")[1].split("<script")[0].strip() == ""


def test_footer_css_rule_does_not_survive_the_footer_element():
    """Dead CSS is dead code: a `footer { ... }` rule with no `<footer>` left to select
    costs bytes against the page's own size budget for nothing. Delete both together."""
    assert "footer {" not in dep_graph._CSS


def test_render_page_reads_the_clock_once():
    """The headline date and the ISO stamp folded below it must name the same day: two
    separate now() calls print two different dates on a render that straddles midnight UTC."""
    import inspect
    assert inspect.getsource(dep_graph.render_page).count("datetime.datetime.now(") == 1


def test_js_the_provenance_fold_is_chrome_and_keeps_the_selection():
    """The fold's <summary> is a control the reader is meant to press, and #evidence now sits
    inside it — so the band joins #facts and #toolbar in the background-click exception. A
    press that opened provenance used to clear the lens the reader had just set."""
    assert "t.closest('#facts, #toolbar, details.provenance')" in dep_graph._JS


def test_build_graph_carries_the_register_synthetic_marker():
    assert dep_graph.build_graph(REG)["synthetic"] is True
    assert dep_graph.build_graph({"KA-01": {"name": "x"}})["synthetic"] is False


def test_render_page_is_self_contained_and_noindex():
    html = _page()
    assert "http://" not in html.replace("https://agent.ai4bcm.org", "")
    assert 'name="robots" content="noindex,nofollow"' in html
    assert "<script src" not in html and "@import" not in html


def test_render_page_stays_under_the_size_budget():
    """Self-contained only stays cheap while the fixed weight — CSS, JS, island — stays
    flat; the live 15-asset register emits ~82 KB. 200 KB is the alarm line for an inlined
    library, an embedded font, or a second copy of the island."""
    assert len(_page(RECORD)) < 200_000


def test_render_page_embeds_nodes_facts_and_focus_hooks():
    html = _page(RECORD)
    for marker in ("KA-01", "cooling plant", "act:act-1", "data-graph",
                   "location.hash", "OWNER MISSING", "SPOF"):
        assert marker in html


def test_render_page_evidence_rows_verbatim_and_base_view_fallback():
    ev = {"register": {"sha": "a3b8f7cc", "written_at": "2026-07-28T13:00:00",
                       "human_line": "✓ Saved and checked: patch applied."},
          "record": None}
    html = _page(evidence=ev)
    assert "a3b8f7cc" in html and "✓ Saved and checked: patch applied." in html
    assert html.index("patch applied.") > html.index('<div id="evidence">')
    assert "no run overlay" in _page()  # register base view fallback line


def test_render_page_maps_numeric_criticality_vocabulary():
    """The live register scores criticality 1/2, not 'critical'/'high' — the status
    colours must fire for both vocabularies (colour never the only carrier)."""
    reg = {"N-01": {"asset_id": "N-01", "name": "numeric crit", "criticality": "1",
                    "depends_on": [], "consumers": []},
           "N-02": {"asset_id": "N-02", "name": "numeric high", "criticality": 2,
                    "depends_on": [], "consumers": []}}
    html = dep_graph.render_page(dep_graph.layout(dep_graph.build_graph(reg)), "x")
    assert 'class="node asset critical"' in html and "critical</text>" not in html.replace(
        "· critical", "")  # mapped class + mapped word on the card
    assert 'class="node asset high"' in html


def test_render_page_legend_is_truthful_about_columns():
    """D3: the legend must state the real column semantics (depth), not the old
    'providers → assets → ...' pipeline wording, which lied about cycles/SCCs."""
    html = _page()
    assert "no recorded upstream dependencies" in html
    assert "dependency depth" in html
    assert "providers →" not in html


def test_render_page_nodes_are_keyboard_tabbable():
    """D4: nodes are keyboard-tabbable now; Enter/Space activation is task 4's JS."""
    html = _page(RECORD)
    assert 'tabindex="0" role="button"' in html


def test_render_page_asset_sub_line_includes_type():
    html = _page()
    assert "KA-01 · technical · critical" in html
    assert "UV-STROM-01 · utility · critical" in html


def test_render_page_asset_sub_line_omits_missing_asset_type_gracefully():
    """No asset_type on the register row must not degrade into a literal None/unknown
    segment — skip the segment entirely, mirroring the file's other missing-field idioms
    (e.g. the row() JS helper skips a fact-table row rather than printing a placeholder)."""
    reg = {"NOTYPE-01": {"asset_id": "NOTYPE-01", "name": "no type", "criticality": "critical",
                         "depends_on": [], "consumers": []}}
    html = dep_graph.render_page(dep_graph.layout(dep_graph.build_graph(reg)), "x")
    assert "NOTYPE-01 · critical" in html
    assert "NOTYPE-01 · None" not in html
    assert "NOTYPE-01 · none" not in html


def test_trunc_sub_limit_raised_to_38():
    """Brief: raise _trunc's sub-line limit 30 → 38 to fit the new asset_type segment."""
    reg = {
        "ASSET-01": {"asset_id": "ASSET-01", "name": "n", "asset_type": "datacenter",
                     "criticality": "low", "depends_on": [], "consumers": []},
        "ASSET-LONG-ID-01": {"asset_id": "ASSET-LONG-ID-01", "name": "n2",
                             "asset_type": "specialized-equipment", "criticality": "critical",
                             "depends_on": [], "consumers": []},
    }
    html = dep_graph.render_page(dep_graph.layout(dep_graph.build_graph(reg)), "x")
    assert "ASSET-01 · datacenter · standard" in html  # 32 chars: fits the new 38 limit
    assert "specialized-equipment · critical" not in html  # 51 chars: still truncated


def test_render_page_has_reset_view_control():
    """D4 marker: the Reset view button ships now; pan/zoom JS wiring is task 5."""
    html = _page()
    assert ">Reset view</button>" in html


def test_render_page_has_reduced_motion_css_block():
    """D4 marker: prefers-reduced-motion CSS lands now; the rules it will govern
    (transitions, the deep-link pulse) ship in task 5."""
    html = _page()
    assert "prefers-reduced-motion" in html


def test_render_page_canvas_pans_instead_of_scrolling():
    """D4: pan replaces scrolling — never both. #canvas clips, and the svg viewport is
    bounded to the window so there is no scrollbar left to compete with the pan."""
    css = dep_graph._CSS
    canvas = css[css.index("#canvas {"):]
    canvas = canvas[: canvas.index("}")]
    assert "overflow: hidden" in canvas and "overflow: auto" not in canvas
    svg = css[css.index("#canvas svg {"):]
    svg = svg[: svg.index("}")]
    # a bounded window is what makes panning mean anything. This was `max-height` until
    # 2026-07-31; it is now a hard `height`, which bounds it more strictly, not less —
    # the element stopped deriving its height from the graph's own aspect ratio.
    assert "height: calc(100vh" in svg


def test_render_page_touch_action_is_scoped_to_the_svg_only():
    """D4: `touch-action: none` hands the svg every gesture, but the rest of the page keeps
    native scrolling and zooming — the declaration may live in an svg rule and nowhere else."""
    css = dep_graph._CSS
    assert css.count("touch-action") == 1
    selector = css[: css.index("touch-action")]  # back up to the rule it sits in
    selector = selector.rsplit("}", 1)[-1].rsplit("*/", 1)[-1].split("{")[0]
    assert "svg" in selector and "body" not in selector and "*" not in selector


def test_render_page_js_pans_and_zooms_the_viewbox_with_pointer_events():
    """D4: the viewBox is the only thing that moves (no CSS transform, no re-layout), drag
    and pinch run on Pointer Events with capture, and the wheel listener must be able to
    preventDefault — so it registers non-passive."""
    js = dep_graph._JS
    assert "setAttribute('viewBox'" in js
    assert "setPointerCapture" in js
    assert "'wheel'" in js and "{passive: false}" in js


def test_js_pan_is_clamped_to_the_content_bounds():
    """KG 2026-07-31, measured on the live page: from the fit view, ONE ordinary 200px
    downward drag moved view.y from 0 to -270 and took the nodes fully in view from 20 to
    7. The graph could be dragged clean out of its own window, and the only way back was a
    "Reset view" button a reader has no reason to look for — "drag it down and not the
    entire screen is the graph anymore". Panning mutated view.x/view.y with no bounds at
    all.

    The clamp lives in draw(), which is the single choke point every view mutation already
    goes through (pan, zoomAt, resetView, revealFocused) — one guard instead of four call
    sites that can each forget it. When the view is larger than the content there is
    nothing to pan to, so the content centres; when it is smaller, the view stays inside
    the content rect."""
    js = dep_graph._JS
    assert js.count("function clampView(") == 1
    draw = js[js.index("function draw()"):]
    draw = draw[: draw.index("\n}")]
    assert "clampView()" in draw  # every mutation path is covered by construction
    clamp = js[js.index("function clampView("):]
    clamp = clamp[: clamp.index("\n}")]
    assert "fit.w" in clamp and "fit.h" in clamp  # bounded by the content extent


def test_js_home_view_never_crops_the_content():
    """KG 2026-07-31, measured: at 2000x900 with the consumer band open, `max-height`
    capped the svg element at 684px while --ar-unfolded (1688/1068) asked for 1045px.
    homeView() derived the home viewBox from that CAPPED box — h = w * b.height/b.width
    = 699 against a content bottom of 1044 — so 345 user units of graph sat outside the
    home view, unreachable, and "Reset view" could not recover them because `fit` itself
    was short. The element box is a presentation artifact; the Python-computed --ar-*
    pair IS the content extent, so the home view must come from there. The element is
    then free to letterbox via preserveAspectRatio without ever hiding content."""
    import re as _re
    js = dep_graph._JS
    home = js[js.index("function homeView()"):]
    home = home[: home.index("\n}")]
    # strip // comments: the rationale below names old expressions on purpose, and a
    # substring pin that reads prose instead of code fails for the wrong reason
    code = "\n".join(_re.sub(r"//.*", "", ln) for ln in home.splitlines())
    assert "--ar-unfolded" in code and "--ar-folded" in code
    # SUPERSEDED 2026-07-31: this used to assert the element box is never measured at all,
    # which was the right rule while the element carried the graph's aspect. Now the element
    # takes the available height, so its shape must be read — but only through Math.max, so
    # it can add margin and never crop. The invariant that mattered is the non-cropping one.
    assert "Math.max" in code


def test_canvas_height_is_not_locked_to_the_graph_aspect():
    """KG 2026-07-31, after three fixes that each corrected the CONTENTS of the window
    without questioning the window itself. `aspect-ratio: var(--ar-*)` locked the svg
    element to the graph's own 4.14:1 ribbon shape, so the canvas measured 288-303px tall
    while max-height already permitted 740 — a letterbox slot using ~40% of the available
    screen height. Zooming in then meant peering through that slot, which is what "I still
    see the issue AFTER I zoom in" was.

    The element now takes the available height directly. The --ar-* pair stays: it is
    still Python's record of the content extent and homeView still reads it (that is what
    keeps the whole graph on screen at rest), it just no longer dictates the element box."""
    css = dep_graph._CSS
    assert "aspect-ratio: var(--ar-folded)" not in css
    assert "aspect-ratio: var(--ar-unfolded)" not in css
    assert "height: calc(100vh - 160px)" in css
    # the vars must survive — homeView and repack both still read them
    assert "--ar-folded:" in dep_graph.render_page(
        dep_graph.collapse_processes(dep_graph.layout(dep_graph.build_graph(REG))), "x")


def test_js_home_view_fills_the_window_without_cropping_the_graph():
    """The viewBox has to match the ELEMENT's aspect or the spare height is letterboxed
    away again — but it must never be narrower or shorter than the content, or the graph
    gets cropped (the failure mode of an earlier fix today). So: take the content extent,
    then grow whichever axis the element's shape demands. Growing never hides anything."""
    js = dep_graph._JS
    home = js[js.index("function homeView()"):]
    home = home[: home.index("\n}")]
    import re as _re
    code = "\n".join(_re.sub(r"//.*", "", ln) for ln in home.splitlines())
    assert "getBoundingClientRect" in code       # the element's shape is now an input again
    assert "--ar-folded" in code and "--ar-unfolded" in code  # …but content is still the floor
    assert "Math.max" in code                    # grow-to-fit, never shrink-to-crop


def test_svg_height_cap_tracks_the_real_chrome_height():
    """The cap exists so the canvas never pushes the page into scrolling. It was a flat
    76vh tuned against the taller chrome that existed before the legend and footer folded
    into Provenance (2026-07-31); with ~160px of chrome left, a viewport-relative subtraction
    both uses the reclaimed space and stays correct if the chrome changes again."""
    import re as _re
    css = dep_graph._CSS
    assert "height: calc(100vh - 160px)" in css
    # the old value survives in the rationale comment by design; assert no live DECLARATION
    # still uses it, rather than banning the string outright
    decls = "\n".join(_re.sub(r"/\*.*?\*/", "", css, flags=_re.S).splitlines())
    assert "76vh" not in decls


def test_render_page_js_clamps_zoom_to_half_and_four_times_the_fit_scale():
    """D4: scale is clamped to [0.5x, 4x] of the fit scale. The clamp is expressed on the
    viewBox width, which is inverse to scale: 4x in = quarter-width viewBox, 0.5x out
    = double-width."""
    js = dep_graph._JS
    assert "fit.w / 4" in js and "fit.w * 2" in js


def test_render_page_js_drag_threshold_gates_every_click_handler():
    """D4: a click that follows more than 5 px of pointer travel is the tail of a drag, not
    a click. The gate sits at the top of the one delegated click entry, so it covers node,
    facts-list and background-clear alike — not just the canvas."""
    js = dep_graph._JS
    assert "Math.hypot(e.clientX - down.x, e.clientY - down.y) <= 5" in js
    body = js[js.index("function onClick("):]
    body = body[: body.index("\n}")]
    assert body.index("moved") < body.index("closest")  # gate runs before any dispatch


def test_render_page_reset_view_restores_the_fit_viewbox():
    """D4: Reset view is a view control, not a lens control — it restores the fit viewBox
    instantly and leaves the current selection alone."""
    js = dep_graph._JS
    assert "function resetView(" in js and "#reset-view" in js
    reset = js[js.index("function resetView("):]
    reset = reset[: reset.index("\n}")]
    assert "homeView()" in reset and "draw()" in reset


def test_render_page_focus_ring_and_deep_link_pulse():
    """D4 polish: the focused node carries a ring — stroke weight, not colour, since colour
    is already the criticality carrier — and arriving on a deep link pulses it."""
    html = _page()
    assert ".node.focused rect.box" in html
    assert "@keyframes node-pulse" in html and ".node.pulse rect.box" in html
    assert "classList.toggle('focused'" in dep_graph._JS
    assert "classList.add('pulse')" in dep_graph._JS


def test_render_page_reduced_motion_kills_the_pulse_but_not_the_pan():
    """prefers-reduced-motion suppresses decoration only. The wildcard override carries
    !important and the pulse rule deliberately does not, so the media block wins whatever
    the specificity; pan/zoom is direct manipulation and is not touched by it."""
    css = dep_graph._CSS
    block = css[css.index("@media (prefers-reduced-motion"):]
    assert "animation: none !important" in block
    assert "touch-action" not in block  # panning survives reduced motion
    pulse = css[css.index(".node.pulse rect.box"):]
    pulse = pulse[: pulse.index("}")]
    assert "animation:" in pulse and "!important" not in pulse


def test_render_page_js_has_one_lens_engine_and_one_hash_writer():
    """D2/D6: a single active lens with replace semantics — exactly one applyLens() and
    one writeHash(). The plan's anti-sprawl rail: no per-lens duplicated apply logic."""
    html = _page()
    assert "applyLens" in html
    assert dep_graph._JS.count("function applyLens(") == 1
    assert dep_graph._JS.count("function writeHash(") == 1


def test_render_page_facts_panel_has_bidirectional_sections_in_order():
    """D1: the panel answers both directions — Depends on (N) then Dependents (N) — then
    Dependent activities (N) and the BIA overlay, with the asset Type row in the core table."""
    js = dep_graph._JS
    for marker in ("'Depends on'", "'Dependents'", "'Dependent activities'", "'BIA run overlay'",
                   "'Type'"):
        assert marker in js
    assert js.index("'Depends on'") < js.index("'Dependents'") < js.index("'Dependent activities'")


# ── D8 adversarial verification (task 7) ─────────────────────────────────────────────

def _end_to_end(register, record=None):
    """The generate() path exactly: build_graph → layout → render_page, which runs
    _annotate itself. Degenerate registers have to survive all four stages, not just
    derivation — a throw here is a traceback inside the post-write regen hook."""
    g = dep_graph.layout(dep_graph.build_graph(register, record))
    return g, dep_graph.render_page(g, "x")


def test_empty_register_renders_a_page_instead_of_throwing():
    """A company folder with a stub or scalar-only register is a real state. Every stage
    must survive it and emit an ordinary page with an empty island."""
    for reg in ({}, {"synthetic": True, "version": 3}):
        g, html = _end_to_end(reg)
        assert g["nodes"] == [] and g["edges"] == []
        assert '"nodes": [], "edges": []' in html
        assert "<svg width=" in html and ">Reset view</button>" in html


def test_cycle_register_renders_end_to_end():
    """Mutual dependencies terminate in derivation and share a column (pinned above);
    this pins the two stages those tests stop short of, _annotate and render_page."""
    g, html = _end_to_end(CYCLE)
    assert _node(g, "A-01")["chain"] == ["B-01"]
    assert _node(g, "A-01")["impact"] == ["B-01"]
    assert 'data-id="A-01"' in html and 'data-id="B-01"' in html
    assert html.count('class="edge depends_on"') == 2  # both directions drawn, no dedupe


def test_all_unowned_register_renders_the_owner_missing_badge_on_every_card():
    """A freshly imported register owns nothing. The page must still render, every card
    must carry the badge, and the owner lens simply has nothing to press."""
    reg = {"A-01": {"asset_id": "A-01", "name": "a", "owner_name": None,
                    "depends_on": ["B-01"], "consumers": [{"dept": "d", "activity": "act"}]},
           "B-01": {"asset_id": "B-01", "name": "b", "owner_name": "",
                    "depends_on": [], "consumers": []}}
    g, html = _end_to_end(reg)
    assert all(n["owner_missing"] for n in g["nodes"] if n["kind"] == "asset")
    assert html.count('class="b-owner"') == 2


def test_dangling_deps_render_as_unmodeled_cards_end_to_end():
    """D7 register-side dangling targets are visible cards, not dropped edges — pinned
    here through render_page, where a missing geometry entry would silently drop them."""
    reg = {"A-01": {"asset_id": "A-01", "name": "a",
                    "depends_on": ["GHOST-01", "GHOST-02"], "consumers": []}}
    g, html = _end_to_end(reg)
    assert [n["id"] for n in g["nodes"] if n["kind"] == "unmodeled"] == ["GHOST-01", "GHOST-02"]
    assert html.count('class="node unmodeled"') == 2
    assert html.count('class="edge depends_on"') == 2


def test_js_gesture_end_listeners_are_bound_on_the_document():
    """A pointer that goes down on the canvas and comes up outside it — a short drag over
    the svg edge, a finger lifted past the border — never delivers pointerup to the svg,
    so its entry stayed in ptrs and the NEXT touch was read as a pinch. The document sees
    both paths: once a gesture captures, the event still bubbles up to it."""
    js = dep_graph._JS
    assert "document.addEventListener(t, endPointer)" in js
    assert "svg.addEventListener(t, endPointer)" not in js


def test_js_wheel_zooms_only_with_ctrl_or_meta_held():
    """KG 2026-07-31: plain wheel over the canvas used to zoom unconditionally, which reads
    as an ordinary scroll gesture the moment the canvas is the thing under the cursor — and
    it is far more often the thing under the cursor now that the legend/footer fold moved
    it higher on the page. A user scrolling to look around silently panned/zoomed away from
    the fit view with no warning, landing exactly on the cropped, cut-off graph KG reported.

    Ctrl/Cmd+wheel still zooms — the modifier every mainstream pan/zoom canvas already uses
    (Figma, Maps, VS Code's minimap), and the one Chrome itself sets on the synthesized wheel
    event for a trackpad pinch, so pinch-to-zoom keeps working with no separate gesture path."""
    js = dep_graph._JS
    wheel = js[js.index("svg.addEventListener('wheel'"):]
    wheel = wheel[: wheel.index("{passive: false}")]
    assert "if (!(e.ctrlKey || e.metaKey)) return;" in wheel
    # the modifier gate is the FIRST thing checked — preventDefault only happens once the
    # canvas has actually decided to own the gesture, so a plain scroll is free to reach
    # the page's own scroll instead of being silently swallowed
    assert wheel.index("if (!(e.ctrlKey || e.metaKey)) return;") < wheel.index("preventDefault")


def test_js_wheel_ignores_a_pure_horizontal_scroll():
    """shift+wheel and a trackpad's sideways swipe deliver deltaY === 0, which the
    `deltaY > 0 ? out : in` ternary read as zoom-in — the graph zoomed on a gesture that
    asked for neither. Scoped to the ctrl/meta-held path now (test above): a plain sideways
    swipe with no modifier never reaches this check at all, it exits at the modifier gate."""
    js = dep_graph._JS
    wheel = js[js.index("svg.addEventListener('wheel'"):]
    wheel = wheel[: wheel.index("{passive: false}")]
    assert "if (!e.deltaY) return;" in wheel
    assert wheel.index("preventDefault") < wheel.index("if (!e.deltaY)")


def test_js_focusing_a_node_pans_it_into_view():
    """A deep-linked or facts-list-pressed node sits outside the home viewBox for any
    register taller than the canvas — the page pulsed a node nobody could see. Focus now
    pans the least that contains it; a node already on screen never moves the view."""
    js = dep_graph._JS
    assert js.count("function revealFocused(") == 1
    body = js[js.index("function applyLens("):]
    assert "revealFocused()" in body[: body.index("\n}")]


def test_asset_sub_line_drops_the_type_rather_than_truncating_the_criticality():
    """Word pass: the live register's longest sub-lines ('UV-ABWASSER-01 · Utility
    (wastewater) · critical', 48 chars) lost the criticality word to the 38-char ellipsis
    on 8 of 15 cards, leaving colour as its only carrier — the exact failure the numeric
    vocabulary test above exists to prevent. The id and the criticality decide an action,
    so the optional type is the segment that goes when the line will not fit."""
    reg = {"UV-ABWASSER-01": {"asset_id": "UV-ABWASSER-01", "name": "wastewater plant",
                              "asset_type": "Utility (wastewater)", "criticality": 1,
                              "depends_on": [], "consumers": []}}
    html = dep_graph.render_page(dep_graph.layout(dep_graph.build_graph(reg)), "x")
    subs = [chunk.split("</text>")[0].split(">")[-1]  # card sub-lines only, not the island
            for chunk in html.split('<text class="sub"')[1:]]
    assert subs == ["UV-ABWASSER-01 · critical"]  # type dropped whole, criticality intact


def test_css_stacks_the_facts_panel_under_the_canvas_on_a_phone():
    """#facts is a fixed 340px sidebar, so at a 390px viewport the canvas was a ~42px
    strip — the graph was effectively absent on a phone, which is exactly where an MCP
    deep link gets opened. Below the breakpoint the panel goes full width underneath."""
    css = dep_graph._CSS
    block = css[css.index("@media (max-width:"):]
    block = block[: block.index("\n}")]
    assert "#facts" in block and "100%" in block


def _contrast(fg: str, bg: str) -> float:
    """WCAG 2.1 contrast ratio, so the AA floor is asserted as the number it is rather
    than as a blacklist of the hex values that happened to fail once."""
    def lum(c: str) -> float:
        ch = []
        for i in (0, 2, 4):
            v = int(c.lstrip("#")[i:i + 2], 16) / 255
            ch.append(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
    lo, hi = sorted((lum(fg), lum(bg)))
    return (hi + 0.05) / (lo + 0.05)


def _declaration(prop: str, selector: str) -> str:
    css = dep_graph._CSS
    value = css[css.index(selector):].split(prop + ":")[1].split(";")[0].strip()
    return _resolve_token(value, css)


def _resolve_token(value: str, css: str) -> str:
    """Follow one level of `var(--x)` back to its :root literal.

    graph.css took its neutrals from brand.py on 2026-08-20, so the chrome colours these
    contrast tests pin are `var(--graphite)` rather than a hex. Without this the tests do
    not fail on a contrast regression — they crash on the indirection, which is worse: a
    crash reads as a broken test and gets skipped, while the ratio goes unchecked.
    """
    match = re.fullmatch(r"var\(\s*(--[\w-]+)\s*\)", value)
    if not match:
        return value
    token = match.group(1)
    declared = re.search(rf"{re.escape(token)}\s*:\s*(#[0-9a-fA-F]{{3,6}})\s*[;}}]", css)
    assert declared, f"{token} is used by a contrast test but never declared in graph.css"
    return declared.group(1)


def test_relocated_footer_note_meets_wcag_aa_on_the_provenance_background():
    """The deep-link grammar sentence moved from a standalone <footer> (page background)
    into the Provenance fold as a `.meta` line (KG 2026-07-31, reclaiming the vertical
    space both it and the legend cost on every load). Re-pinned against where the text
    actually sits now: `.meta`'s ink on `details.provenance`'s own white background, not
    the retired footer selector and its retired background assumption."""
    assert _contrast(_declaration("color", ".meta {"), "#ffffff") >= 4.5


def test_activity_card_sub_line_meets_wcag_aa_against_its_tint():
    """Measured: the .node text.sub ink on the old #f5f3ff activity tint is 4.34:1. Only
    a page with a BIA record overlay draws activity cards, so this one was dormant."""
    assert _contrast("#64748b", _declaration("fill", ".node.activity rect.box {")) >= 4.5


# ── v2.1: column headers + SPOF/unowned property lenses ──────────────────────────────
# KG read the horizontal axis as a category ("SPOF I have on the far left but also in the
# middle") because the canvas carried no column labels — the truth lived only in legend
# prose. The axis is depth; SPOF and ownership are orthogonal properties of an asset.

CHAIN3 = {  # A → B → C: three depth levels, so the plural label has something to sit on
    "A-01": {"asset_id": "A-01", "name": "a", "depends_on": ["B-01"], "consumers": []},
    "B-01": {"asset_id": "B-01", "name": "b", "depends_on": ["C-01"], "consumers": []},
    "C-01": {"asset_id": "C-01", "name": "c", "depends_on": [], "consumers": []},
}


def test_column_labels_name_the_depth_axis():
    """One header per occupied column, derived from the layout's own col numbers: column 0
    is the root of the dependency chain, later asset columns are depth, and the reserved
    activity/process columns say what they hold."""
    g = dep_graph.layout(dep_graph.build_graph(REG, RECORD))
    labels = dep_graph._column_labels(g)
    assert labels[0] == "No upstream dependencies"
    assert labels[1] == "Depends on 1 level"
    assert labels[_node(g, "act:act-1")["col"]] == "BIA activities"
    assert labels[_node(g, "proc:qs:HACCP evidence")["col"]] == "Dependent activities"


def test_column_labels_pluralise_past_the_first_level():
    labels = dep_graph._column_labels(dep_graph.layout(dep_graph.build_graph(CHAIN3)))
    assert labels[1] == "Depends on 1 level"
    assert labels[2] == "Depends on 2 levels"


def test_column_labels_cover_exactly_the_occupied_columns():
    """_geometry collapses an empty reserved column to a thin spacer — a header over one
    would label a strip of nothing (the live register has no BIA record, so its activity
    column is empty)."""
    g = dep_graph.layout(dep_graph.build_graph(REG))
    assert set(dep_graph._column_labels(g)) == {n["col"] for n in g["nodes"]}


def test_column_label_follows_the_dominant_kind_in_the_column():
    """A record-side unmodeled card has no recorded dependencies, so it lands in column 0
    alongside real assets. Both share the depth label, and the dominant kind decides."""
    g = dep_graph.layout(dep_graph.build_graph(REG, RECORD))
    col0 = {n["kind"] for n in g["nodes"] if n["col"] == 0}
    assert col0 == {"asset", "unmodeled"}  # genuinely mixed
    assert dep_graph._column_labels(g)[0] == "No upstream dependencies"


def test_render_page_draws_one_header_over_every_occupied_column():
    html = _page(RECORD)
    g = dep_graph.layout(dep_graph.build_graph(REG, RECORD))
    assert html.count('class="col-head"') == len({n["col"] for n in g["nodes"]})
    for label in ("No upstream dependencies", "Depends on 1 level", "BIA activities",
                  "Dependent activities"):
        assert f">{label}</text>" in html


def test_column_headers_clear_the_first_row_of_cards():
    """The band above row 0 is reserved in the geometry, so a header never sits on a card."""
    html = _page(RECORD)
    head_y = float(html.split('class="col-head"')[1].split('y="')[1].split('"')[0])
    card_y = min(float(c.split('y="')[1].split('"')[0])
                 for c in html.split('<rect class="box" ')[1:])
    assert head_y < card_y


def test_column_headers_are_muted_ink_and_not_clickable():
    """An axis label is recessive text, never a control: page text ink, and pointer-events
    off so a press on one falls through to the canvas the same as empty space."""
    css = dep_graph._CSS
    block = css[css.index(".col-head {"):]
    block = block[: block.index("}")]
    assert "pointer-events: none" in block
    assert _contrast(_declaration("fill", ".col-head {"), "#f8fafc") >= 4.5


def test_render_page_legend_says_a_flag_is_a_property_not_a_column():
    """KG's actual question, answered in one sentence: the axis is depth, and SPOF and
    ownership are orthogonal to it."""
    html = _page()
    assert "properties of the asset, not of a column" in html
    assert "at any depth" in html


def test_island_carries_the_flags_the_property_lenses_read():
    """D6: spof / owner_missing are derived in Python and must reach the island verbatim —
    the flag lenses read them and derive nothing themselves."""
    html = _page()
    island = json.loads(html.split('id="data-graph">')[1].split("</script>")[0]
                        .replace("<\\/", "</"))
    by_id = {n["id"]: n for n in island["nodes"]}
    assert by_id["UV-STROM-01"]["spof"] is True
    assert by_id["UV-STROM-01"]["owner_missing"] is True
    assert by_id["KA-01"]["spof"] is True and by_id["KA-01"]["owner_missing"] is False


def test_js_keeps_one_lens_engine():
    """The anti-sprawl rail: a new lens type may not fork the engine into per-lens copies
    (the preset lenses — owner / criticality / property chips — were cut 2026-08-18)."""
    js = dep_graph._JS
    for fn in ("applyLens", "writeHash", "keepSet", "fillFacts", "setLens"):
        assert js.count(f"function {fn}(") == 1


def _fetch_factory(register, record=None):
    def fetch(company, path):
        if path.endswith("dependency-register.json"):
            return {"content": json.dumps(register), "size": 1}
        if record is not None and path.endswith("bia-record.json"):
            return {"content": json.dumps(record), "size": 1}
        return {"error": "file not found: " + path}
    return fetch


def test_answer_resolves_exact_id_and_builds_deep_link():
    out = dep_graph.answer("marschkamp", "KA-01", _fetch_factory(REG))
    assert out["asset"]["id"] == "KA-01"
    assert out["deep_link"] == "https://agent.ai4bcm.org/demo/graph/marschkamp/#KA-01"
    assert out["depends_on_chain"][0]["id"] == "UV-STROM-01"


def test_answer_resolves_unique_name_substring():
    out = dep_graph.answer("marschkamp", "cooling", _fetch_factory(REG))
    assert out["asset"]["id"] == "KA-01"


def test_answer_ambiguous_returns_candidates():
    reg = dict(REG)
    reg["KA-02"] = {"asset_id": "KA-02", "name": "cooling tower", "depends_on": [],
                    "consumers": []}
    out = dep_graph.answer("marschkamp", "cooling", _fetch_factory(reg))
    assert "error" in out
    assert {c["id"] for c in out["candidates"]} == {"KA-01", "KA-02"}


def test_answer_includes_overlay_only_when_record_names_the_asset():
    out = dep_graph.answer("marschkamp", "KA-01", _fetch_factory(REG, RECORD))
    assert out["overlay"]["activities"] == ["act-1"]
    out2 = dep_graph.answer("marschkamp", "UV-STROM-01", _fetch_factory(REG, RECORD))
    assert out2["overlay"] is None


def test_answer_includes_dependents_and_counts():
    out = dep_graph.answer("marschkamp", "UV-STROM-01", _fetch_factory(REG))
    assert out["dependents"][0]["id"] == "KA-01"
    assert out["counts"] == {"depends_on": 0, "dependents": 1, "consumers": 0}
    assert "depend on it" in out["human_line"]


def test_answer_unreadable_register_is_a_legible_error():
    def fetch(company, path):
        return {"error": f"unknown company '{company}'"}
    out = dep_graph.answer("evilco", "KA-01", fetch)
    assert "error" in out and "evilco" in out["error"]


# ── collapsed consumer layout — canonical since the 2026-07-29 A/B/C decision ──────────
# (docs/graph-abc-layout-variants.md — git history — records the evaluation and the
# rejected wrap/full-column alternatives.) generate() applies collapse_processes; the pure-pipeline tests
# above exercise render_page without it, which stays a supported path.


def _collapsed_graph():
    return dep_graph.collapse_processes(dep_graph.layout(dep_graph.build_graph(REG)))


def test_collapse_marks_processes_folded_and_adds_one_group_node():
    g = _collapsed_graph()
    procs = [n for n in g["nodes"] if n["kind"] == "process"]
    assert procs and all(p["collapsed"] for p in procs)
    groups = [n for n in g["nodes"] if n["kind"] == "process_group"]
    assert len(groups) == 1
    grp = groups[0]
    assert sorted(grp["procs"]) == sorted(p["id"] for p in procs)
    assert grp["col"] == procs[0]["col"]  # the card stands where the column was
    assert grp["name"] == f"{len(procs)} dependent activities"


def test_collapse_page_folds_process_cards_into_the_band():
    """Aufklappen (KG's live-review word): every process ships as a real card in the
    fold band docked under the group card, class `folded` so CSS hides it until the
    active lens keeps it, with its consumes edge carrying to-folded to hide alongside.
    The bundled fan — one always-visible consumes edge per feeding asset into the group
    card — stays the base view's connection, and the svg carries both Python-computed
    aspect ratios."""
    g = _collapsed_graph()
    html = dep_graph.render_page(g, "x")
    svg_half, island_half = html.split('id="data-graph">')
    island = json.loads(island_half.split("</script>")[0].replace("<\\/", "</"))
    by_id = {n["id"]: n for n in island["nodes"]}
    assert by_id["proc:zerlegung:chilled cutting"]["collapsed"] is True
    assert 'class="node process folded"' in svg_half  # the card exists, hidden in base
    assert 'class="edge consumes to-folded"' in svg_half  # its real edge folds with it
    fan = svg_half.count('data-dst="group:processes"')
    assert fan == 1  # KA-01 is REG's one feeder
    assert "--ar-folded:" in svg_half and "--ar-unfolded:" in svg_half
    assert "fold-head" not in svg_half  # the group card IS the band's header (rev 4)
    # The band opens whole (kept lit, rest ghosted — the asset block's own grammar),
    # and the opened stack drops its sheets. Rejections from KG's second live review.
    assert "body.unfolded .node.folded," in dep_graph._CSS
    assert "body.unfolded .node.process_group .stack { display: none; }" in dep_graph._CSS
    geo = dep_graph._geometry(g)
    assert geo["proc:zerlegung:chilled cutting"][1] > geo["KA-01"][1]  # band sits below
    # …and right-aligned: the band's left edge stays right of the rightmost asset's
    # left edge, so ghosted consumers never sit under the depth columns (third review).
    band_left = min(geo[p][0] for p in ("proc:zerlegung:chilled cutting",
                                        "proc:qs:HACCP evidence"))
    assert band_left > geo["KA-01"][0]
    # The fan edge is presentation, not data: a feeder's consumer list stays its real
    # processes — the group card must never appear as one of its own consumers.
    assert by_id["KA-01"]["procs"] == ["proc:zerlegung:chilled cutting",
                                      "proc:qs:HACCP evidence"]
    assert len(html) < 200_000


def test_selection_hides_unrelated_edges_and_folded_ghosts():
    """KG 2026-07-31: a selection shows only its own lines, and the fold band
    materializes only the selected asset's consumers — never dimmed ghost rows
    under the BIA-activities header. Base view (no lens -> keep null -> no dim)
    is untouched; clicking the group card keeps all 33 procs + feeders via
    keepSet's procs/keep_extra walk, so 'show everything' stays one click away."""
    assert ".edge.dim { display: none; }" in dep_graph._CSS
    assert "body.unfolded .node.folded.dim { display: none; }" in dep_graph._CSS
    # .edge.dim alone is (0,2,0) and loses to body.unfolded .edge.to-folded's
    # (0,3,1), so without this rule a dimmed to-folded edge keeps rendering at
    # .12 opacity once the card it points at goes display:none — a faint line
    # to nothing (controller ruling, 2026-07-31).
    assert "body.unfolded .edge.to-folded.dim { display: none; }" in dep_graph._CSS
    # the generic dim rule must survive for non-folded NODES (ghost context grammar)
    assert ".dim { opacity: .12; }" in dep_graph._CSS


def test_band_right_column_is_the_group_card_s_own():
    """The repack target, in geometry rather than in prose: the band's right-hand
    column shares the group card's x, so a selection small enough to fit there stacks
    directly under the card it unfolded from — and under 'Dependent activities', not
    under the 'BIA activities' header the band's LEFT column inherits."""
    g = _collapsed_graph()
    geo = dep_graph._geometry(g)
    band_xs = sorted({geo[n["id"]][0] for n in g["nodes"] if n.get("collapsed")})
    assert band_xs[-1] == geo["group:processes"][0]


def test_js_repacks_the_open_band_and_refits_the_canvas():
    """KG 2026-07-31: cards frozen in their authored slots strand a 3-card selection
    rows apart while the canvas still reserves the whole 33-card band — 'huge gaps ANY
    frontend designer would immediately flag'. So the lens repacks: survivors take
    contiguous slots, the group card's column first, and the canvas re-fits to what is
    actually open. Python still authors every slot; the JS only chooses which ones get
    used, which is why the moved cards' edges have to be re-drawn here too."""
    js = dep_graph._JS
    assert js.count("function repack(") == 1
    assert "var refit = repack(open);" in js  # one call site, inside applyLens
    assert "open.length <= RIGHT.length ? RIGHT : SLOTS" in js
    # A moved card drags its consumes edge with it: the endpoints come off the live BOX
    # map, never off the baked `d`. route() is _edge_svg's curve, kept in step by the
    # control-offset pin below.
    assert "function route(" in js
    assert ".edge.to-folded" in js
    # …and the canvas stops reserving height nobody is using. The floor and the pad are
    # read off Python's own two ratios, so no margin is re-derived in here.
    assert "svg.style.setProperty('--ar-unfolded'" in js
    # revealFocused must read the repacked position, not the authored rect attributes,
    # or focusing a process pans to the slot the card just left.
    assert "rect.box'" not in js.split("function revealFocused(")[1][:400]


def test_js_route_mirrors_the_python_edge_curve():
    """route() is the one place the JS owns geometry, and a drifted control offset is
    invisible until a curve visibly misses a card. Pin both sides to the same numbers:
    _edge_svg's 60 for the left/right branches and 70 for the same-column arc."""
    js = dep_graph._JS
    src = dep_graph.__file__
    import pathlib
    py = pathlib.Path(src).read_text(encoding="utf-8").split("def _edge_svg(")[1]
    py = py.split("\ndef ")[0]
    for off in ("60", "70"):
        assert f"+ {off}" in py or f"- {off}" in py, f"_edge_svg lost its {off} offset"
        assert f"+ {off})" in js, f"route() lost its {off} offset"


def test_collapse_keep_extra_relights_the_consumer_relationship():
    """The visible answer to a click is kept cards plus hot edges, and keep_extra is how
    the group card joins in: a feeding asset keeps the group (its fan edge goes hot), a
    collapsed process keeps the group (clicking it in the facts panel lights the
    asset→consumers path), and the group keeps its feeders. The JS tuple must read
    the field, or the canvas disagrees with the data."""
    g = _collapsed_graph()
    assert _node(g, "KA-01")["keep_extra"] == ["group:processes"]
    assert "keep_extra" not in _node(g, "UV-STROM-01")  # no consumers, no fan edge
    assert _node(g, "proc:zerlegung:chilled cutting")["keep_extra"] == ["group:processes"]
    assert _node(g, "group:processes")["keep_extra"] == ["KA-01"]
    assert "'keep_extra'" in dep_graph._JS


def test_no_iso_process_word_reaches_the_reader():
    """KG 2026-07-31: in ISO 22301 a "process" transforms inputs into outputs and exists
    whether or not anyone runs a BIA. A register `consumers` line is not that — it exists
    only relative to one asset, which is why the slaughter operation appears in the
    marschkamp band six separate times, once per resource it needs. ISO/GPG calls what
    these are activities, so every reader-facing string says "dependent activities" and
    the BIA-record column keeps "BIA activities": one was assessed, the other is the
    register's own note of which departmental work needs an asset.

    The internal vocabulary is deliberately NOT renamed — `kind: "process"` reaches CSS
    classes and the island contract, and moving it buys the reader nothing."""
    html = _page(RECORD)
    import re as _re
    visible = " ".join(_re.findall(r"<text[^>]*>(.*?)</text>", html, _re.S))
    visible += " " + html.split('class="legend">')[1].split("</div>")[0]
    assert "process" not in visible.lower(), visible
    assert "Dependent activities" in visible and "BIA activities" in visible
    assert 'class="node process"' in html  # the kind itself stays put
    for label in ("'Dependent activities'", "'Department'"):
        assert label in dep_graph._JS


def test_asset_panel_does_not_label_register_prose_as_the_iso_metric():
    """method.json defines MTPD as "the earliest time horizon at which worst-case impact
    reaches threshold 4" — one of six values. The register puts free prose in a field
    called `mtpd` on every one of its 15 assets ("Can slaughter but not cut; labour
    bottleneck = core risk"), and in ISO 22301 an MTPD belongs to an activity, not to a
    resource, so an asset has none to state. Rendering that under a row labelled MTPD is
    the most misleading thing on the page: an unfamiliar label makes a BCM manager ask,
    a familiar one misused makes them assume. The record's own MTPD is untouched — there
    the field is a real method horizon and the label is correct."""
    js = dep_graph._JS
    asset = js.split("if (n.kind === 'asset')")[1].split("} else if")[0]
    assert "row('Impact of loss', n.mtpd)" in asset
    assert "row('MTPD'" not in asset
    assert "row('RTO (stated)', n.rto)" in asset  # asserted in the register, not derived
    # the BIA record's activity keeps the real metric under its real name
    act = js.split("if (n.kind === 'activity')")[1].split("} else")[0]
    assert "row('MTPD', n.mtpd)" in act


def test_activity_dept_reconciles_the_two_activity_vocabularies():
    """The join key that was missing. The register records `dept` on every consumers
    line; the BIA record's activity had no department at all, so nothing could ever
    line the two up — the live symptom being UV-ABWASSER-01, which lists schlachtung as
    a consumer while AN-SCHLACHT-01 does not list it back (a Stage-3 finding).

    Reconciliation only: `dept_acts` is a facts-panel rollup, never an edge and never in
    the keep set. The relationship is part-of, and this canvas's horizontal axis promises
    dependency depth — see the _column_labels docstring."""
    rec = {"activities": [dict(RECORD["activities"][0], dept="qs")]}
    g = dep_graph._annotate(dep_graph.layout(dep_graph.build_graph(REG, rec)))
    act = _node(g, "act:act-1")
    assert act["dept"] == "qs"
    assert act["dept_acts"] == ["proc:qs:HACCP evidence"]
    # a record with no dept keeps the honest absence rather than an empty-list claim
    plain = dep_graph._annotate(dep_graph.layout(dep_graph.build_graph(REG, RECORD)))
    assert "dept_acts" not in _node(plain, "act:act-1")
    js = dep_graph._JS
    assert "dept_acts" in js
    assert "'dept_acts'" not in js  # NOT in keepSet's tuple: a rollup lights no cards


def test_collapse_group_column_keeps_the_consumers_label():
    g = _collapsed_graph()
    grp = next(n for n in g["nodes"] if n["kind"] == "process_group")
    assert dep_graph._column_labels(g)[grp["col"]] == "Dependent activities"


def test_group_facts_branch_lives_inside_the_one_nodefacts():
    js = dep_graph._JS
    assert js.count("function nodeFacts(") == 1
    assert "process_group" in js and "dept_count" in js


def test_group_card_subtitle_reacts_to_selection():
    """KG 2026-07-31: clicking different assets must visibly change the group card.
    The subtitle swaps to 'N of <total> in this selection' under an asset focus and
    restores the server-rendered original otherwise. A noun phrase, not 'N serve':
    m === 1 is the common case and has no verb to agree with (KG's copy ruling).
    Pinned by text, house style."""
    js = dep_graph._JS
    assert "var groupSub0 = null;" in js
    assert "in this selection" in js
    assert '[data-id="group:processes"] text.sub' in js
    # Both halves of the state contract, in order: a capture that moved below the rewrite
    # would bank the rewritten line, and clearing the lens would never restore the original.
    assert "groupSub0 = gsub.textContent" in js
    assert "gsub.textContent = groupSub0;" in js
    assert js.index("groupSub0 = gsub.textContent") < js.index("in this selection")


def test_generate_produces_the_collapsed_layout(tmp_path, monkeypatch):
    """The wiring that makes the collapse canonical: generate() — the CLI's and the
    BIA-write regen hook's shared path — applies collapse_processes, so the live page
    carries the group card and the fold band, not the 33-row column."""
    monkeypatch.setattr(dep_graph, "PUBLIC", tmp_path)
    out = dep_graph.generate("marschkamp", _fetch_factory(REG))
    html = (tmp_path / "marschkamp" / "index.html").read_text(encoding="utf-8")
    assert out.endswith("index.html")
    assert 'class="node process_group"' in html
    assert 'class="node process folded"' in html


# ── renderer build stamp ─────────────────────────────────────────────────────
def test_renderer_stamp_names_the_loaded_module():
    """The fresh case: loaded sha equals on-disk sha, so the stamp is a plain id."""
    stamp = dep_graph._renderer_stamp()
    assert dep_graph._RENDERER_SHA in stamp
    assert "STALE" not in stamp


def test_renderer_stamp_shouts_when_the_process_is_stale(monkeypatch):
    """A long-lived service holds the module it imported at start. If the file on disk
    moves on and nobody restarts, the page must say so — that silence cost three days."""
    monkeypatch.setattr(dep_graph, "_RENDERER_SHA", "deadbeef")
    stamp = dep_graph._renderer_stamp()
    assert "STALE" in stamp and "deadbeef" in stamp
    assert "Restart the service" in stamp


# ─────────────────────────────── CLI target resolution (2026-08-04)

def test_cli_targets_warns_when_all_silently_means_one_room():
    """`dep_graph.py all` takes its targets from BIA_WORKFLOW_COMPANIES, which is unset in
    any interactive shell — so the documented "regenerate both pages" step regenerated
    marschkamp only, with no warning (found 2026-08-04)."""
    targets, warning = dep_graph.cli_targets("all", ("marschkamp",))
    assert targets == ("marschkamp",)
    assert warning and "BIA_WORKFLOW_COMPANIES" in warning


def test_cli_targets_is_quiet_when_all_really_means_all():
    targets, warning = dep_graph.cli_targets("all", ("marschkamp", "marschkamp-demo"))
    assert targets == ("marschkamp", "marschkamp-demo")
    assert warning is None


def test_cli_targets_named_room_is_never_a_warning():
    targets, warning = dep_graph.cli_targets("marschkamp-demo", ("marschkamp",))
    assert targets == ("marschkamp-demo",) and warning is None


# ─────────────────────────────── data freshness + regen safety (2026-08-04)

def test_headline_dates_the_data_not_the_render(tmp_path, monkeypatch):
    """The visible headline read 'Updated <today>' off the render clock, so a page built
    from a three-day-old register still claimed today's date — the confusion behind the
    2026-08-03 owner report. It must name when each SOURCE was written."""
    html = dep_graph.render_page(
        dep_graph.layout(dep_graph.build_graph(REG)), "x",
        evidence={"register": {"sha": "57d6a3ba", "written_at": "2026-07-30T06:30:51+00:00"},
                  "record": {"sha": "539e2fe0", "written_at": "2026-08-04T09:20:44+00:00"}})
    headline = html.split('<div class="meta">')[1].split("</div>")[0]
    assert "30 Jul 2026" in headline and "04 Aug 2026" in headline
    assert "register" in headline and "BIA record" in headline


def test_headline_says_read_live_when_nothing_is_banked():
    html = dep_graph.render_page(dep_graph.layout(dep_graph.build_graph(REG)), "x")
    headline = html.split('<div class="meta">')[1].split("</div>")[0]
    assert "read live" in headline


def test_generate_tolerates_a_genuinely_absent_record(tmp_path, monkeypatch):
    """A room that has not run a BIA yet is a register-only page, not an error."""
    monkeypatch.setattr(dep_graph, "PUBLIC", tmp_path)
    out = dep_graph.generate("newroom", _fetch_factory(REG))          # no record in factory
    assert out.endswith("index.html")
    assert "act:" not in (tmp_path / "newroom" / "index.html").read_text(encoding="utf-8")


def test_generate_refuses_to_publish_when_the_record_read_fails(tmp_path, monkeypatch):
    """A transient read failure must NOT publish an activity-less page over a good one.
    Only a real 404 means 'no BIA here'; anything else aborts the regen."""
    monkeypatch.setattr(dep_graph, "PUBLIC", tmp_path)

    def fetch(company, path):
        if path.endswith("dependency-register.json"):
            return {"content": json.dumps(REG), "size": 1}
        return {"error": "file too large to read (9999999 bytes)"}

    try:
        dep_graph.generate("marschkamp", fetch)
    except RuntimeError as exc:
        assert "bia-record" in str(exc) or "record" in str(exc)
    else:
        raise AssertionError("generate published a page despite a failed record read")
    assert not (tmp_path / "marschkamp" / "index.html").exists()


# ───────────────────── the 404 half of the same hole (2026-08-10 incident)
# The 2026-08-04 guard above only covers NON-404 read errors. On 2026-08-10 the room's
# SharePoint `output` folder was renamed for a pre-beta reset, so the next regen — two
# minutes later — hit a perfectly legitimate 404, took the "this room has not run a BIA
# yet" branch, and republished the public page with 49 nodes and zero activities over one
# that carried 51 and two. It regressed twice that evening and nobody noticed for two days.
# A 404 only means "no BIA here" for a room whose page never had one: the published page
# is the second witness, and losing activities against it takes an explicit force.

def test_generate_refuses_to_publish_fewer_activities_than_the_live_page(tmp_path,
                                                                        monkeypatch):
    monkeypatch.setattr(dep_graph, "PUBLIC", tmp_path)
    dep_graph.generate("marschkamp", _fetch_factory(REG, RECORD))
    good = (tmp_path / "marschkamp" / "index.html").read_text(encoding="utf-8")
    assert "act:act-1" in good

    try:
        dep_graph.generate("marschkamp", _fetch_factory(REG))   # record now 404s
    except RuntimeError as exc:
        assert "activit" in str(exc)
    else:
        raise AssertionError("generate published a page that lost every BIA activity")
    assert (tmp_path / "marschkamp" / "index.html").read_text(encoding="utf-8") == good


def test_generate_force_retires_a_rooms_bia_on_purpose(tmp_path, monkeypatch):
    """Retiring a room's BIA is legitimate — it just has to be said out loud."""
    monkeypatch.setattr(dep_graph, "PUBLIC", tmp_path)
    dep_graph.generate("marschkamp", _fetch_factory(REG, RECORD))
    out = dep_graph.generate("marschkamp", _fetch_factory(REG), force=True)
    assert out.endswith("index.html")
    assert "act:act-1" not in (tmp_path / "marschkamp" / "index.html").read_text(
        encoding="utf-8")


def test_generate_republishes_a_page_that_keeps_its_activities(tmp_path, monkeypatch):
    """The hot path — the ordinary register write regenerates with the SAME activities —
    and the one combination the other cases miss: a non-zero prior count that must publish.
    An over-counting reader would raise here on every normal write, and _graph_regen's
    never-blocks boundary would swallow it into a log line nobody reads: the same silent
    class as the bug this guard fixes, pointing the other way."""
    monkeypatch.setattr(dep_graph, "PUBLIC", tmp_path)
    dep_graph.generate("marschkamp", _fetch_factory(REG, RECORD))
    out = dep_graph.generate("marschkamp", _fetch_factory(REG, RECORD))
    assert out.endswith("index.html")
    assert "act:act-1" in (tmp_path / "marschkamp" / "index.html").read_text(
        encoding="utf-8")


def test_generate_keeps_republishing_a_register_only_page(tmp_path, monkeypatch):
    """The guard must stay silent for a room that has never run a BIA: zero is not fewer
    than zero, so its page keeps rebuilding on every register write."""
    monkeypatch.setattr(dep_graph, "PUBLIC", tmp_path)
    dep_graph.generate("newroom", _fetch_factory(REG))
    out = dep_graph.generate("newroom", _fetch_factory(REG))     # second regen, page exists
    assert out.endswith("index.html")
    assert "act:" not in (tmp_path / "newroom" / "index.html").read_text(encoding="utf-8")


def test_generate_publishes_a_page_that_gains_activities(tmp_path, monkeypatch):
    """The guard is one-directional — the ordinary BIA write adds activities and must not
    be mistaken for the incident."""
    monkeypatch.setattr(dep_graph, "PUBLIC", tmp_path)
    dep_graph.generate("marschkamp", _fetch_factory(REG))
    dep_graph.generate("marschkamp", _fetch_factory(REG, RECORD))
    assert "act:act-1" in (tmp_path / "marschkamp" / "index.html").read_text(
        encoding="utf-8")


def test_bank_and_regen_never_forces_past_the_guard(tmp_path, monkeypatch):
    """The post-write hook is precisely the caller worth blocking: a write that leaves the
    record unreadable is the incident, not an authorisation to publish without it."""
    import graph_files

    monkeypatch.setattr(dep_graph, "PUBLIC", tmp_path)
    dep_graph.generate("marschkamp", _fetch_factory(REG, RECORD))
    good = (tmp_path / "marschkamp" / "index.html").read_text(encoding="utf-8")

    try:
        dep_graph.bank_and_regen("marschkamp", graph_files.RECORD_SAVE_PATH, b"{}",
                                 {"verification": {}}, _fetch_factory(REG))
    except RuntimeError as exc:
        assert "activit" in str(exc)
    else:
        raise AssertionError("the regen hook forced past the activity guard")
    assert (tmp_path / "marschkamp" / "index.html").read_text(encoding="utf-8") == good


def test_css_and_js_are_asset_files_read_at_import():
    """D asset split (2026-08-18): the page's CSS and JS live in graph.css / graph.js beside
    this module and are inlined at import — the rendered page stays self-contained (see
    test_render_page_is_self_contained_and_noindex), the module stops carrying 800 lines of
    string literal."""
    from pathlib import Path
    here = Path(dep_graph.__file__).resolve().parent
    assert dep_graph._CSS == (here / "graph.css").read_text(encoding="utf-8")
    assert dep_graph._JS == (here / "graph.js").read_text(encoding="utf-8")


def test_renderer_stamp_covers_the_asset_files_too(tmp_path, monkeypatch):
    """After the asset split the JS/CSS change without dep_graph.py changing — the stale
    detector must read all three, or an edited graph.js renders under a 'fresh' stamp."""
    assert dep_graph._RENDERER_SHA == dep_graph._source_sha()
    monkeypatch.setattr(dep_graph, "_HERE", tmp_path)
    for name in ("dep_graph.py", "graph.css", "graph.js"):
        (tmp_path / name).write_text("changed", encoding="utf-8")
    assert dep_graph._source_sha() != dep_graph._RENDERER_SHA
    assert "STALE" in dep_graph._renderer_stamp()
