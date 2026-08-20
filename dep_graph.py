"""Dependency-graph derivation + layout + renderer (board Open item 9, DEP-01/OUT-01).

Pure core: build_graph/upstream_chain/downstream_chain/layout/_annotate/render_page do
no I/O. The CLI and the regen hook (graph_files) do the fetching. Spec:
/opt/brain/docs/superpowers/specs/2026-07-28-dependency-graph-design.md
"""
from __future__ import annotations

import datetime
import hashlib
import html as _html
import json
from pathlib import Path

BASE_URL = "https://agent.ai4bcm.org/demo/graph"
PUBLIC = Path(__file__).resolve().parent / "public" / "graph"
# One source of truth: render_page writes the island behind this tag and the publish guard
# in generate() reads the previous page back through it.
_ISLAND_TAG = '<script type="application/json" id="data-graph">'

_ASSET_FIELDS = ("name", "asset_type", "bereich", "owner_name", "criticality", "rto",
                 "rpo", "mtpd", "spof", "redundancy", "supplier", "contract_status")


# The sha of this module's own source, captured AT IMPORT — deliberately not at render
# time. A long-lived service holds the module it imported at start; if the file on disk
# changes afterwards the process keeps rendering the old code, and reading the file at
# render time would report the NEW sha and hide exactly that. Cost three days on
# 2026-07-31: the fold and canvas-height fixes landed 2h42m after the service started,
# every save-triggered regeneration silently reverted the published page, and the page
# said nothing about which code drew it.
# Since the asset split (2026-08-18) the renderer is three files — the module plus graph.css
# and graph.js — so the stamp hashes all three, or an edited graph.js would ship as "fresh".
_HERE = Path(__file__).resolve().parent


def _source_sha() -> str:
    h = hashlib.sha256()
    for name in ("dep_graph.py", "graph.css", "graph.js"):
        h.update((_HERE / name).read_bytes())
    return h.hexdigest()[:8]


_RENDERER_SHA = _source_sha()


def _renderer_stamp() -> str:
    """Provenance fragment naming the renderer, and shouting when it is stale."""
    try:
        on_disk = _source_sha()
    except OSError:  # source moved or unreadable — the loaded sha is still the truth
        on_disk = _RENDERER_SHA
    if on_disk == _RENDERER_SHA:
        return f' · renderer <code>{_RENDERER_SHA}</code>'
    return (f' · renderer <code>{_RENDERER_SHA}</code> — <strong>STALE: '
            f'<code>{on_disk}</code> is on disk. Restart the service to load it.</strong>')


def build_graph(register: dict, record: dict | None = None) -> dict:
    nodes, edges = [], []
    assets = {k: v for k, v in register.items() if isinstance(v, dict)}
    for aid, a in assets.items():
        node = {"id": aid, "kind": "asset",
                "owner_missing": not a.get("owner_name"),
                "pp4_issue": bool(a.get("pp4_issue")),
                "quality_flag_count": len(a.get("quality_flags") or [])}
        node.update({f: a.get(f) for f in _ASSET_FIELDS})
        nodes.append(node)
        for dep in a.get("depends_on") or []:
            edges.append({"src": aid, "dst": dep, "kind": "depends_on"})
            if dep not in assets and not any(n["id"] == dep for n in nodes):
                # D7: dangling register-side dep — mirror the record-side unmodeled
                # branch below so it's a visible node instead of a silently dropped edge.
                nodes.append({"id": dep, "kind": "unmodeled"})
    seen_procs = {}
    for aid, a in assets.items():
        for c in a.get("consumers") or []:
            key = f"proc:{c.get('dept')}:{c.get('activity')}"
            if key not in seen_procs:
                seen_procs[key] = {"id": key, "kind": "process", "dept": c.get("dept"),
                                   "activity": c.get("activity"), "need": c.get("need"),
                                   "consumer_mtpd": c.get("consumer_mtpd")}
                nodes.append(seen_procs[key])
            edges.append({"src": aid, "dst": key, "kind": "consumes"})
    for act in (record or {}).get("activities", []):
        # Referee-validated records carry name/owner and no id at all — tolerate both
        # vocabularies the way _crit_class does, never degrade to the act:? stub.
        act_id = f"act:{act.get('id') or act.get('name') or '?'}"
        # `dept` is the only field that can line a BIA activity up against the register's
        # own consumers lines, which carry a dept and nothing else in common. Optional:
        # records written before 2026-07-31 have none, and an absent dept must stay absent
        # rather than becoming an empty claim (see _annotate).
        nodes.append({"id": act_id, "kind": "activity", "name": act.get("name"),
                      "owner_name": act.get("owner_name") or act.get("owner"),
                      "dept": act.get("dept"),
                      "mtpd": act.get("mtpd"),
                      "recovery_target": act.get("recovery_target")})
        for dep in act.get("dependencies") or []:
            if isinstance(dep, dict):
                dep = dep.get("id")
            if not isinstance(dep, str):
                continue  # id-less dep = unmodeled BIA finding
            if dep not in assets and not any(n["id"] == dep for n in nodes):
                nodes.append({"id": dep, "kind": "unmodeled"})
            edges.append({"src": act_id, "dst": dep, "kind": "activity_dep"})
    # The register's own data-classification marker, carried through so the page can caption
    # itself honestly. It is a bool at the register root, so the assets comprehension above
    # already skips it; this is the only thing that reads it.
    return {"nodes": nodes, "edges": edges, "synthetic": bool(register.get("synthetic"))}


def layout(graph: dict) -> dict:
    """Variant-A layered columns: providers left of dependents, activities then
    processes right of all assets, rows stable-sorted by (bereich, id)."""
    by_id = {n["id"]: n for n in graph["nodes"]}
    deps: dict[str, list[str]] = {}  # dependent -> providers
    for e in graph["edges"]:
        if e["kind"] == "depends_on" and e["dst"] in by_id:
            deps.setdefault(e["src"], []).append(e["dst"])
    assets = [n for n in graph["nodes"] if n["kind"] in ("asset", "unmodeled")]
    # ponytail: O(n²) reachability + SCC-by-mutual-reach — register-scale graphs
    # (dozens of assets); switch to Tarjan if a register ever grows past that.
    reach: dict[str, set[str]] = {}
    for n in assets:
        seen: set[str] = set()
        stack = list(deps.get(n["id"], []))
        while stack:
            p = stack.pop()
            if p not in seen:
                seen.add(p)
                stack.extend(deps.get(p, []))
        reach[n["id"]] = seen

    def scc(aid: str) -> frozenset:
        mutual = {b for b in reach.get(aid, set()) if aid in reach.get(b, set())}
        return frozenset(mutual | {aid})

    depth_cache: dict[frozenset, int] = {}

    def gdepth(group: frozenset) -> int:
        # Condensed over SCCs the graph is a DAG, so plain recursion terminates
        # and every cycle member shares one column.
        if group not in depth_cache:
            providers = {scc(p) for a in group for p in deps.get(a, []) if p not in group}
            depth_cache[group] = 1 + max((gdepth(pg) for pg in providers), default=-1)
        return depth_cache[group]

    for n in assets:
        n["col"] = gdepth(scc(n["id"]))
    max_col = max((n["col"] for n in assets), default=0)
    for n in graph["nodes"]:
        if n["kind"] == "activity":
            n["col"] = max_col + 1
        elif n["kind"] == "process":
            n["col"] = max_col + 2
    for col in {n["col"] for n in graph["nodes"]}:
        members = sorted((n for n in graph["nodes"] if n["col"] == col),
                         key=lambda n: (n.get("bereich") or "", n["id"]))
        for row, n in enumerate(members):
            n["row"] = row
    return graph


# ── collapsed consumer layout (canonical since 2026-07-29) ───────────────────────────
# Runs AFTER layout() and only moves col/row or adds one derived node. Chosen over the
# full column and the wrapped column in the A/B/C evaluation — the measurements and
# the rejected alternatives are recorded in docs/graph-abc-layout-variants.md (git history,
# retired 2026-08-18).


def collapse_processes(graph: dict) -> dict:
    """The base view answers asset structure; consumers fold out on demand.

    Every process keeps its island entry AND its card: `collapsed` nodes render into a
    dept-sorted fold band below the graph (see _geometry) that CSS hides until the
    active lens keeps them — aufklappen, the expected behaviour named in KG's first
    live review. Focus an asset and its consumers unfold connected by their real
    edges; focus the group card and the whole band opens. The group card stays the
    base view's single stand-in, with one bundled fan edge per feeding asset."""
    procs = [n for n in graph["nodes"] if n["kind"] == "process"]
    if not procs:
        return graph
    gid = "group:processes"
    proc_ids = {p["id"] for p in procs}
    for p in procs:
        p["collapsed"] = True
        # A collapsed process is reached from the facts panel, and its visible answer is
        # the asset→consumers path: chain assets light anyway, keep_extra lights the
        # group card too, which sets the fan edges from its feeders hot.
        p["keep_extra"] = [gid]
    # The bundled fan: one consumes edge per feeding asset, ending at the group card.
    # Without it the consumer side reads as severed — the first live review's actual
    # finding. Feeders keep the group on focus (their fan edge goes hot), and the group
    # keeps its feeders, so clicking the summary answers "what feeds the consumers".
    feeders = sorted({e["src"] for e in graph["edges"]
                      if e["kind"] == "consumes" and e["dst"] in proc_ids})
    for n in graph["nodes"]:
        if n["id"] in feeders:
            n["keep_extra"] = [gid]
    graph["edges"] += [{"src": a, "dst": gid, "kind": "consumes"} for a in feeders]
    depts = {p["dept"] for p in procs if p.get("dept")}
    graph["nodes"].append({
        "id": gid, "kind": "process_group",
        "name": f"{len(procs)} dependent activities",
        "dept_count": len(depts),
        # Dept-sorted so the facts list reads as grouped blocks, not island build order.
        "procs": [p["id"] for p in sorted(procs,
                                          key=lambda p: (p.get("dept") or "", p["id"]))],
        "keep_extra": feeders,
        "col": procs[0]["col"], "row": 0})
    return graph


def upstream_chain(graph: dict, asset_id: str) -> list[dict]:
    by_id = {n["id"]: n for n in graph["nodes"]}
    dep_edges = [(e["src"], e["dst"]) for e in graph["edges"] if e["kind"] == "depends_on"]
    chain, seen, frontier, depth = [], {asset_id}, [asset_id], 0
    while frontier:
        depth += 1
        frontier = [d for s, d in dep_edges if s in frontier and d not in seen]
        for d in dict.fromkeys(frontier):  # stable order, dedupe within level
            seen.add(d)
            n = by_id.get(d, {})
            chain.append({"id": d, "depth": depth, "spof": bool(n.get("spof")),
                          "owner_missing": bool(n.get("owner_missing"))})
    return chain


def downstream_chain(graph: dict, asset_id: str) -> list[dict]:
    """Mirror of upstream_chain over reversed depends_on edges: the transitive
    dependents of asset_id (its blast radius) instead of the providers it needs."""
    by_id = {n["id"]: n for n in graph["nodes"]}
    dep_edges = [(e["src"], e["dst"]) for e in graph["edges"] if e["kind"] == "depends_on"]
    chain, seen, frontier, depth = [], {asset_id}, [asset_id], 0
    while frontier:
        depth += 1
        frontier = [s for s, d in dep_edges if d in frontier and s not in seen]
        for d in dict.fromkeys(frontier):  # stable order, dedupe within level
            seen.add(d)
            n = by_id.get(d, {})
            chain.append({"id": d, "depth": depth, "spof": bool(n.get("spof")),
                          "owner_missing": bool(n.get("owner_missing"))})
    return chain


def _annotate(graph: dict) -> dict:
    """Precompute per-node island fields — id-lists only, no dicts, so the JSON island
    stays small. The JS reads these directly; it never re-derives edges or re-maps the
    criticality vocabulary itself. assets: chain (upstream ids), impact (downstream ids,
    via downstream_chain), procs + acts (direct consumers/activity dependents), crit
    (canonical _crit_class word). processes: feeds (direct assets) + chain (feeds then
    their upstream, deduped). activities: chain (unchanged). unmodeled: impact + acts."""
    # procs means consuming PROCESSES: variant B's bundled fan edges are also kind
    # "consumes" but end at the group card, and without this filter every feeding
    # asset listed the group as one of its own consumers in the facts panel.
    process_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "process"}
    for n in graph["nodes"]:
        kind = n["kind"]
        if kind == "asset":
            n["chain"] = [c["id"] for c in upstream_chain(graph, n["id"])]
            n["impact"] = [c["id"] for c in downstream_chain(graph, n["id"])]
            n["procs"] = [e["dst"] for e in graph["edges"]
                          if e["kind"] == "consumes" and e["src"] == n["id"]
                          and e["dst"] in process_ids]
            n["acts"] = [e["src"] for e in graph["edges"]
                        if e["kind"] == "activity_dep" and e["dst"] == n["id"]]
            n["crit"] = _crit_class(n.get("criticality"))
        elif kind == "activity":
            direct = [e["dst"] for e in graph["edges"]
                      if e["kind"] == "activity_dep" and e["src"] == n["id"]]
            chain = list(dict.fromkeys(direct))
            for d in direct:
                chain += [c["id"] for c in upstream_chain(graph, d) if c["id"] not in chain]
            n["chain"] = chain
            # The reconciliation the missing dept used to make impossible: the register's
            # consumers lines for this activity's own department. A ROLLUP, not a
            # dependency — the same operation described once per resource it needs — so it
            # gets a facts-panel section and no edge. The canvas axis promises dependency
            # depth (see _column_labels), and part-of does not belong on it.
            if n.get("dept"):
                n["dept_acts"] = [p["id"] for p in graph["nodes"]
                                  if p["kind"] == "process" and p.get("dept") == n["dept"]]
        elif kind == "process":
            feeds = list(dict.fromkeys(e["src"] for e in graph["edges"]
                                       if e["kind"] == "consumes" and e["dst"] == n["id"]))
            n["feeds"] = feeds
            chain = list(feeds)
            for f in feeds:
                chain += [c["id"] for c in upstream_chain(graph, f) if c["id"] not in chain]
            n["chain"] = chain
        elif kind == "unmodeled":
            n["impact"] = [c["id"] for c in downstream_chain(graph, n["id"])]
            n["acts"] = [e["src"] for e in graph["edges"]
                        if e["kind"] == "activity_dep" and e["dst"] == n["id"]]
    return graph


# ── renderer ─────────────────────────────────────────────────────────────────────────
_NODE_W, _COL_GAP, _SPACER, _MARGIN = 200, 40, 48, 24
_KIND_H = {"asset": 64, "unmodeled": 64, "activity": 56, "process": 44}
_KIND_PITCH = {"asset": 88, "unmodeled": 88, "activity": 72, "process": 54}
_HEAD_BAND, _HEAD_BASE = 32, 13  # strip reserved above row 0, and the header baseline in it


def _crit_class(v) -> str:
    """Status mapping for both register vocabularies: 'critical'/'high' and 1/2."""
    s = str(v).strip().lower()
    if s in ("critical", "1"):
        return "critical"
    if s in ("high", "2"):
        return "high"
    return "standard"


def _geometry(graph: dict) -> dict:
    """Pixel geometry from the abstract grid — presentation only. Empty reserved
    columns collapse to a thin spacer and the dense consumer column gets a tighter
    pitch; the layout()-assigned cols/rows never change."""
    if not graph["nodes"]:
        return {}
    occupied = {n["col"] for n in graph["nodes"]}
    col_x, x = {}, _MARGIN
    for c in range(max(occupied) + 1):
        col_x[c] = x
        x += (_NODE_W + _COL_GAP) if c in occupied else _SPACER
    geo = {n["id"]: (col_x[n["col"]],
                     _MARGIN + _HEAD_BAND + n["row"] * _KIND_PITCH.get(n["kind"], 88),
                     _NODE_W, _KIND_H.get(n["kind"], 64))
           for n in graph["nodes"] if not n.get("collapsed")}
    # Collapsed nodes fold out (aufklappen) into a static band hanging RIGHT-ALIGNED
    # below the group card's region — never under the depth columns, which read as part
    # of the left-to-right dependency axis (KG's third live review). Two columns filling
    # top-down keep the dept-sorted order contiguous, the band's left edge stays right
    # of the asset block, and the canvas width never grows, so the base view keeps its
    # scale. The cards ship in the SVG and CSS hides them until the active lens keeps
    # them — geometry stays Python's, visibility the lens's.
    folded = sorted((n for n in graph["nodes"] if n.get("collapsed")),
                    key=lambda n: (n.get("dept") or "", n["id"]))
    if folded:
        right_edge = max((x + w for x, _y, w, _h in geo.values()),
                         default=_MARGIN + _NODE_W)
        x0 = right_edge - 2 * _NODE_W - _COL_GAP
        # The band docks directly under the group card (KG: the cards unfold as if they
        # were part of it) — so it clears only the cards it actually sits under, the
        # ones overlapping its own x-range, not the whole asset block. Anchoring on the
        # global bottom left a huge empty gap between card and content.
        band_y = max((y + h for x, y, w, h in geo.values() if x + w > x0),
                     default=_MARGIN) + 16
        per = -(-len(folded) // 2)  # ceil: left column takes the odd card
        for i, n in enumerate(folded):
            c, r = divmod(i, per)
            geo[n["id"]] = (x0 + c * (_NODE_W + _COL_GAP),
                            band_y + r * _KIND_PITCH["process"],
                            _NODE_W, _KIND_H["process"])
    return geo


def _column_labels(graph: dict) -> dict[int, str]:
    """The horizontal axis, said out loud — one header per OCCUPIED column, read off the
    cols layout() already assigned and never recomputed.

    Position encodes dependency depth and nothing else, which is exactly what a reader
    cannot see: SPOF cards sit in the leftmost column and in the middle, so the axis gets
    read as a category and then contradicted. For an asset the column index IS the depth by
    construction (layout() sets col = gdepth), so the label is the depth; activities and
    processes get columns of their own, so those are named for what they hold.

    A column is labelled for the kind that dominates it. asset and unmodeled cards share the
    depth label, and layout() puts every activity and process strictly right of every asset
    column, so a column can never mix two label FAMILIES — there is no mixed case for the
    legend to explain."""
    labels = {}
    for col in sorted({n["col"] for n in graph["nodes"]}):
        kinds = [n["kind"] for n in graph["nodes"] if n["col"] == col]
        top = max(set(kinds), key=lambda k: (kinds.count(k), k))  # ties break deterministically
        labels[col] = ("Dependent activities" if top in ("process", "process_group")
                       else "BIA activities" if top == "activity"
                       else "No upstream dependencies" if col == 0
                       else f"Depends on {col} level{'s' if col > 1 else ''}")
    return labels

# The page's CSS and JS are asset files beside this module, inlined at import so the
# rendered page stays self-contained (no external request — pinned by
# test_render_page_is_self_contained_and_noindex). Edit graph.css / graph.js, not this file.
_CSS = (_HERE / "graph.css").read_text(encoding="utf-8")
_JS = (_HERE / "graph.js").read_text(encoding="utf-8")

# ponytail: vanilla JS, no layout logic — Python precomputed cols/rows/chains; the JS
# only toggles classes and fills the facts panel from the JSON island.



def _esc(v) -> str:
    return _html.escape(str(v)) if v is not None else ""


def _trunc(s: str, n: int = 26) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"  # never a space before the ellipsis


def _node_svg(n: dict, geo: dict) -> str:
    x, y, w, h = geo[n["id"]]
    kind = n["kind"]
    classes = ["node", kind]
    if n.get("collapsed"):
        classes.append("folded")  # hidden until the active lens keeps it
    label = n.get("name") or n.get("activity") or n["id"]
    sub = n["id"]
    if kind == "asset":
        crit = _crit_class(n.get("criticality"))
        classes.append(crit)
        crit_label = crit if n.get("criticality") is not None else "unrated"
        # The id and the criticality decide an action, so neither may be what the 38-char
        # ellipsis eats. asset_type is the optional one: rendered when the whole line fits,
        # dropped whole when it doesn't (and skipped entirely when the register omits it,
        # never a literal None).
        sub = f'{n["id"]} · {crit_label}'
        if n.get("asset_type"):
            typed = f'{n["id"]} · {n["asset_type"]} · {crit_label}'
            if len(typed) <= 38:
                sub = typed
    elif kind == "process":
        sub = f"{n.get('dept') or ''} · activity"
    elif kind == "process_group":
        # The name already carries the count; the sub line says what pressing it does.
        sub = f"{n.get('dept_count') or 0} departments · select for the list"
    elif kind == "activity":
        sub = f"{n['id']} · BIA activity"
    elif kind == "unmodeled":
        sub = "unmodeled dependency"
    name_y, sub_y = (22, 38) if h >= 64 else ((20, 36) if h >= 56 else (17, 32))
    badges = []
    if n.get("spof"):
        badges.append('<tspan class="b-spof">SPOF</tspan>')
    if n.get("owner_missing"):
        badges.append('<tspan class="b-owner">OWNER MISSING</tspan>')
    if n.get("pp4_issue"):
        badges.append('<tspan class="b-pp4">PP4</tspan>')
    if n.get("quality_flag_count"):
        badges.append(f'<tspan class="b-flags">⚑{n["quality_flag_count"]}</tspan>')
    badge_text = ""
    if badges and h >= 64:
        badge_text = (f'<text x="{x + 10}" y="{y + 54}">' + '<tspan> </tspan>'.join(badges)
                      + "</text>")
    # The group card's stacked-cards affordance: two offset sheets behind the box say
    # "there are more inside" without a word. Own class, not `box`, so the focus ring's
    # stroke-width bump reaches only the front card.
    stack = ""
    if kind == "process_group":
        stack = (f'<rect class="stack s2" x="{x + 10}" y="{y + 10}" width="{w}" '
                 f'height="{h}" rx="8"/>'
                 f'<rect class="stack s1" x="{x + 5}" y="{y + 5}" width="{w}" '
                 f'height="{h}" rx="8"/>')
    return (f'<g class="{" ".join(classes)}" data-id="{_esc(n["id"])}" '
            f'tabindex="0" role="button">'
            f'<title>{_esc(label)}</title>'
            f'{stack}<rect class="box" x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/>'
            f'<text class="name" x="{x + 10}" y="{y + name_y}">{_esc(_trunc(str(label)))}</text>'
            f'<text class="sub" x="{x + 10}" y="{y + sub_y}">{_esc(_trunc(sub, 38))}</text>'
            f"{badge_text}</g>")


def _header_svg(graph: dict, geo: dict) -> str:
    """One label per occupied column, left-aligned on that column's cards. Every node in a
    column shares an x, so the position comes straight from the geometry — no second copy
    of the column maths."""
    x_of: dict[int, int] = {}
    for n in graph["nodes"]:
        # A folded card's x is its fold-band slot, not its column — anchoring a header
        # there put "Processes (consumers)" on top of column 0's header. Headers anchor
        # on the cards that are actually in their column.
        if n["id"] in geo and not n.get("collapsed"):
            x_of.setdefault(n["col"], geo[n["id"]][0])
    heads = "".join(f'<text class="col-head" x="{x_of[col]}" y="{_MARGIN + _HEAD_BASE}">'
                    f"{_esc(label)}</text>"
                    for col, label in _column_labels(graph).items() if col in x_of)
    # No separate fold-band header: the band docks directly under the group card, and
    # that card — "33 consuming processes · select for the list" — IS its label.
    return heads


def _edge_svg(e: dict, geo: dict, folded: frozenset = frozenset()) -> str:
    ga, gb = geo.get(e["src"]), geo.get(e["dst"])
    if ga is None or gb is None:  # dangling register reference — nothing to draw
        return ""
    fold_cls = " to-folded" if (e["src"] in folded or e["dst"] in folded) else ""
    ax, ay, aw, ah = ga
    bx, by, bw, bh = gb
    ya, yb = ay + ah // 2, by + bh // 2
    if bx < ax:  # provider left of dependent
        d = f"M{ax},{ya} C{ax - 60},{ya} {bx + bw + 60},{yb} {bx + bw},{yb}"
    elif bx > ax:
        d = f"M{ax + aw},{ya} C{ax + aw + 60},{ya} {bx - 60},{yb} {bx},{yb}"
    else:  # same column (cycle members): arc out on the right
        d = f"M{ax + aw},{ya} C{ax + aw + 70},{ya} {bx + bw + 70},{yb} {bx + bw},{yb}"
    return (f'<path class="edge {e["kind"]}{fold_cls}" data-src="{_esc(e["src"])}" '
            f'data-dst="{_esc(e["dst"])}" d="{d}"/>')


def _data_age(reg_ev: dict | None, rec_ev: dict | None) -> str:
    """When the page's SOURCES were last written — never when the page was rendered.

    Both stamps are banked per write in evidence.json; before 2026-08-04 they were only
    visible inside the collapsed Provenance fold while the headline showed the render
    clock. A reader could not tell a fresh page from a stale one without opening the fold.
    """
    parts = []
    for label, ev in (("BIA record", rec_ev), ("register", reg_ev)):
        at = (ev or {}).get("written_at")
        if not at:
            continue
        try:
            when = datetime.datetime.fromisoformat(at).strftime("%d %b %Y")
        except ValueError:  # a banked stamp we cannot parse is shown raw, never dropped
            when = at
        parts.append(f"{label} {when}")
    return " · ".join(parts) if parts else "read live at generation"


def _ev_row(label: str, ev: dict | None, absent_line: str) -> str:
    if not ev:
        return (f'<div class="ev"><span class="src">{label}</span>'
                f'<span class="absent">{absent_line}</span></div>')
    bits = [f'<span class="src">{label}</span>']
    if ev.get("sha"):
        bits.append(f'<code>{_esc(ev["sha"])}</code>')
    if ev.get("written_at"):
        bits.append(f'<span class="when">{_esc(ev["written_at"])}</span>')
    if ev.get("human_line"):
        bits.append(f'<span class="line">{_esc(ev["human_line"])}</span>')
    elif not ev.get("written_at"):
        bits.append('<span class="absent">read live at generation — no banked write '
                    "evidence</span>")
    out = '<div class="ev">' + " ".join(bits)
    return out + "</div>"


def render_page(graph: dict, company: str, evidence: dict | None = None) -> str:
    _annotate(graph)  # precompute island fields; the JS never derives edges itself
    geo = _geometry(graph)
    width = max((x + w for x, _y, w, _h in geo.values()), default=360) + _MARGIN
    height = max((y + h for _x, y, _w, h in geo.values()), default=160) + _MARGIN
    # Collapsed nodes render as `folded` cards in a band below the graph, hidden by CSS
    # until the active lens keeps them; their edges carry to-folded and hide with them.
    folded_ids = frozenset(n["id"] for n in graph["nodes"] if n.get("collapsed"))
    edges_svg = "".join(_edge_svg(e, geo, folded_ids) for e in graph["edges"])
    nodes_svg = "".join(_node_svg(n, geo) for n in graph["nodes"] if n["id"] in geo)
    heads_svg = _header_svg(graph, geo)
    # The island is exactly the model a later Cytoscape-class explorer would consume
    # (spec D2) — keep it a faithful {nodes, edges} dump, build nothing else for it.
    island = json.dumps({"nodes": graph["nodes"], "edges": graph["edges"]},
                        ensure_ascii=False).replace("</", "<\\/")
    # With a fold band the element's aspect is state-dependent: the page opens at the
    # folded height and grows (aufklappen) when the lens keeps folded cards — homeView()
    # reads the element box, so the view follows without any JS layout. Pages without
    # folded content get no style attribute and keep their attribute-intrinsic aspect.
    fold_style = ""
    if folded_ids:
        folded_h = max(y + h for nid, (_x, y, _w, h) in geo.items()
                       if nid not in folded_ids) + _MARGIN
        fold_style = (f' style="--ar-folded:{width}/{folded_h};'
                      f'--ar-unfolded:{width}/{height}"')
    reg_ev = (evidence or {}).get("register")
    rec_ev = (evidence or {}).get("record")
    now = datetime.datetime.now(datetime.UTC)  # one clock read: the headline date and the
    generated_at = now.isoformat(timespec="seconds")  # ISO stamp must never name two days
    sha_bit = f' · register sha <code>{_esc(reg_ev["sha"])}</code>' if reg_ev and reg_ev.get("sha") else ""
    # Data classification is read off the register, never hardcoded: this caption used to print
    # unconditionally, so a real company's page would have carried "synthetic demonstration
    # data" over live data — the one caption you must not show a room. Absent marker = no
    # claim; the Provenance fold below already carries the write evidence either way.
    renderer_bit = _renderer_stamp()
    data_note = " · synthetic demonstration data" if graph.get("synthetic") else ""
    # The header leads with the one line a reader acts on; the ISO stamp, the sha and the
    # evidence rows are provenance, and provenance folds away (KG front-end review 4).
    # That headline used to carry the RENDER clock ("Updated <today>"), which is the one
    # thing a reader must not confuse with data age: the page rebuilds on every write, so
    # a three-day-old register still read as today's date (2026-08-03 — an applied owner
    # change looked un-applied, and the only contradicting stamp was inside the fold).
    data_age = _data_age(reg_ev, rec_ev)
    headline = (f"Synthetic demonstration data · {data_age}"
                if graph.get("synthetic") else data_age)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex,nofollow">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(company)} — resource dependency graph</title>
<style>{_CSS}</style>
</head>
<body>
<header>
<p class="home"><a href="/demo/kb/">BIA Workflow</a></p>
<h1>{_esc(company)} — resource dependency graph</h1>
<div class="meta">{headline}</div>
</header>
<details class="provenance">
<summary class="meta">Provenance</summary>
<div class="meta">Generated {generated_at}{sha_bit}{renderer_bit}{data_note}</div>
<div id="evidence">
{_ev_row("Register", reg_ev, "no banked write evidence")}
{_ev_row("BIA record", rec_ev, "no run overlay — register base view")}
</div>
<div class="legend">Left = no recorded upstream dependencies; horizontal distance =
dependency depth, and each column is headed on the canvas with what it means. The two
rightmost columns are both activities: <em>BIA activities</em> were assessed in the BIA record
(impact grid, MTPD, owner); <em>dependent activities</em> are the register's note on each asset
of whose work needs it — one line per asset, so one operation appears once per resource it
needs. A column says only how deep an asset sits — SPOF and
missing ownership are properties of the asset, not of a column, so they turn up at any depth.
Other badges: PP4 (handoff issue), ⚑n (quality flags).</div>
<div class="meta">Register-derived view · the Provenance fold holds the latest verified write per source
· location.hash deep-links a node (e.g. #KA-01)</div>
</details>
<div id="toolbar"><span class="tb-end">Esc clears
<button type="button" id="reset-view">Reset view</button></span></div>
<main>
<div id="canvas"><svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"{fold_style}>
{heads_svg}
{edges_svg}
{nodes_svg}
</svg></div>
<aside id="facts"></aside>
</main>
{_ISLAND_TAG}{island}</script>
<script>{_JS}</script>
</body>
</html>
"""


def answer(company: str, asset: str, fetch) -> dict:
    """Deterministic dependency answer for the MCP tool — live reads, stores nothing.

    Resolution: exact asset_id → unique case-insensitive substring over id+name →
    else an error listing candidates. All derivation goes through build_graph /
    upstream_chain; this never derives edges itself."""
    reg = fetch(company, "03_Dependencies/dependency-register.json")
    if "error" in reg:
        return {"error": f"cannot read register for {company}: {reg['error']}"}
    try:
        register = json.loads(reg["content"])
    except ValueError:
        return {"error": f"register for {company} is not valid JSON"}
    record = None
    rec = fetch(company, "output/bia-record.json")
    if "error" not in rec:
        try:
            record = json.loads(rec["content"])
        except ValueError:
            record = None  # corrupt overlay never breaks the register answer
    assets = {k: v for k, v in register.items() if isinstance(v, dict)}
    q = (asset or "").strip()
    if q in assets:
        aid = q
    else:
        ql = q.casefold()
        hits = [k for k, v in assets.items()
                if ql in k.casefold() or ql in str(v.get("name") or "").casefold()]
        if len(hits) == 1:
            aid = hits[0]
        elif hits:
            return {"error": f"ambiguous asset '{asset}' — offer the candidates back",
                    "candidates": [{"id": k, "name": assets[k].get("name")} for k in hits]}
        else:
            return {"error": f"no asset matches '{asset}' in the {company} register",
                    "candidates": [{"id": k, "name": v.get("name")}
                                   for k, v in assets.items()]}
    g = build_graph(register, record)
    node = next(n for n in g["nodes"] if n["id"] == aid)
    chain = upstream_chain(g, aid)
    dependents = downstream_chain(g, aid)
    consumers = [{"dept": c.get("dept"), "activity": c.get("activity"),
                  "need": c.get("need"), "consumer_mtpd": c.get("consumer_mtpd")}
                 for c in assets[aid].get("consumers") or []]
    acts = [e["src"] for e in g["edges"]
            if e["kind"] == "activity_dep" and e["dst"] == aid]
    overlay = ({"activities": [a.split("act:", 1)[1] for a in dict.fromkeys(acts)]}
               if acts else None)
    n_spof = sum(1 for c in chain if c["spof"])
    counts = {"depends_on": len(chain), "dependents": len(dependents),
              "consumers": len(consumers)}
    human_line = (f"{node.get('name') or aid} depends on {len(chain)} upstream asset"
                  f"{'s' if len(chain) != 1 else ''}"
                  + (f" ({n_spof} SPOF)" if n_spof else "")
                  + f"; {len(dependents)} downstream asset"
                  f"{'s' if len(dependents) != 1 else ''} depend on it"
                  + f"; {len(consumers)} process{'es' if len(consumers) != 1 else ''}"
                  " consume it — see the graph link.")
    return {"asset": node, "depends_on_chain": chain, "dependents": dependents,
            "consumers": consumers, "counts": counts, "overlay": overlay,
            "deep_link": f"{BASE_URL}/{company}/#{aid}",
            "register_sha": hashlib.sha256(reg["content"].encode("utf-8")).hexdigest()[:8],
            "human_line": human_line}


def bank_and_regen(company: str, rel: str, data: bytes, result: dict, fetch) -> None:
    """Post-verified-write hook body: bank the write's own verification evidence,
    then regenerate the company page. Called from graph_files under a never-blocks
    boundary — raising here only produces a logged warning there."""
    import graph_files  # lazy: graph_files lazy-imports this module's hook target

    out_dir = PUBLIC / company
    out_dir.mkdir(parents=True, exist_ok=True)
    ev_file = out_dir / "evidence.json"
    evidence = json.loads(ev_file.read_text(encoding="utf-8")) if ev_file.exists() else {}
    key = "register" if rel == graph_files.REGISTER_PATH else "record"
    verification = result.get("verification") or {}
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    evidence[key] = {
        "sha": hashlib.sha256(data).hexdigest()[:8],
        "written_at": now,
        "human_line": verification.get("human_line"),
    }
    if result.get("amendment"):
        # Correction audit (update_bia_activity): a durable list — per-key banking above
        # is replaced on every save, amendments only ever append.
        evidence.setdefault("amendments", []).append({**result["amendment"], "at": now})
    ev_file.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    generate(company, fetch)  # never force: a write that loses the record IS the incident


def _published_activities(index: Path) -> int:
    """How many BIA activity nodes the page already on disk carries — read back out of the
    page's own data island. render_page escapes `</` inside the island, so the first
    `</script>` after the tag is always the real close. A missing, unreadable or
    unparseable page counts zero: it is not the good page this guard exists to protect,
    and failing closed on it would wedge every future regen of that room."""
    try:
        html = index.read_text(encoding="utf-8")
        start = html.index(_ISLAND_TAG) + len(_ISLAND_TAG)
        nodes = json.loads(html[start:html.index("</script>", start)])["nodes"]
    except (OSError, ValueError, KeyError):
        return 0
    return sum(1 for n in nodes if n.get("kind") == "activity")


def generate(company: str, fetch, force: bool = False) -> str:
    # Same relative path as graph_files.REGISTER_PATH — literal here so the pure core
    # never imports the Graph lane.
    reg = fetch(company, "03_Dependencies/dependency-register.json")
    if "error" in reg:
        raise RuntimeError(f"cannot read register for {company}: {reg['error']}")
    record = None
    rec = fetch(company, "output/bia-record.json")
    if "error" not in rec:
        record = json.loads(rec["content"])
    elif "not found" not in rec["error"]:
        # A genuine 404 means this room has not run a BIA yet — a register-only page is
        # the correct output. ANY other read error (throttle, expiry, size cap) must not
        # publish an activity-less page over a good one: the write that triggered this
        # regen already reported success, so a silently gutted page is the one failure
        # nobody would notice. Raising leaves the previous page in place; the caller's
        # never-blocks boundary logs it. Contract: read_file's 404 says "file not found".
        raise RuntimeError(f"cannot read bia-record for {company}: {rec['error']}")
    out_dir = PUBLIC / company
    out_dir.mkdir(parents=True, exist_ok=True)
    ev_file = out_dir / "evidence.json"
    evidence = json.loads(ev_file.read_text(encoding="utf-8")) if ev_file.exists() else None
    if not (evidence or {}).get("register"):
        # CLI-generated view (e.g. the write-refused demo company): carry the live
        # register sha so the Provenance fold stays honest without banked write evidence.
        evidence = dict(evidence or {})
        evidence["register"] = {
            "sha": hashlib.sha256(reg["content"].encode("utf-8")).hexdigest()[:8]}
    # Canonical since the 2026-07-29 A/B/C evaluation: consumers collapse into the
    # group card and fold out under the lens (decision record in git history).
    g = collapse_processes(layout(build_graph(json.loads(reg["content"]), record)))
    index = out_dir / "index.html"
    acts = sum(1 for n in g["nodes"] if n["kind"] == "activity")
    was = 0 if force else _published_activities(index)
    if acts < was:
        # The 404 branch above is right about a room that never ran a BIA and blind to one
        # whose record just went missing. On 2026-08-10 the room's `output` folder was
        # renamed for a pre-beta reset; the regen two minutes later read a perfectly
        # legitimate 404 and republished the public page with zero activities over two —
        # twice that evening, unnoticed for two days. The published page is the second
        # witness the 404 alone cannot be: losing activities against it is an accident
        # until a human says otherwise.
        raise RuntimeError(
            f"refusing to publish {company}: BIA activity count would drop from {was} "
            f"to {acts}. Check the record is really gone (renamed or archived folder?) "
            "before re-running with --force.")
    page = render_page(g, company, evidence)
    index.write_text(page, encoding="utf-8")
    return str(index)


def cli_targets(arg: str, companies) -> tuple[tuple, str | None]:
    """Which rooms a CLI run regenerates, plus a warning when `all` silently means one.

    `all` resolves through BIA_WORKFLOW_COMPANIES, which only systemd sets — so `all` from
    an interactive shell regenerated marschkamp alone and said nothing about the other
    served room (found 2026-08-04). No silent scope reduction: say what was skipped.
    """
    if arg and arg != "all":
        return (arg,), None
    if len(companies) > 1:
        return tuple(companies), None
    return tuple(companies), (
        "WARNING: 'all' resolved to " + ", ".join(companies) + " only — "
        "BIA_WORKFLOW_COMPANIES is unset or single-valued, so any other served room was "
        "NOT regenerated. Re-run as BIA_WORKFLOW_COMPANIES=room1,room2 dep_graph.py all")


if __name__ == "__main__":  # ponytail: thin CLI, logic lives above
    import sys

    import graph_files

    # --force publishes a page carrying fewer BIA activities than the live one — the
    # deliberate "retire this room's BIA" case. Without it generate() refuses; see there.
    force = "--force" in sys.argv[1:]
    rooms = [a for a in sys.argv[1:] if a != "--force"]
    targets, warning = cli_targets(rooms[0] if rooms else "all", graph_files.COMPANIES)
    if warning:
        print(warning, file=sys.stderr)
    for comp in targets:
        print(generate(comp, graph_files.read_file, force=force))
