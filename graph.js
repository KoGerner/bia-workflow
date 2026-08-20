
/* Lens engine (D1). Exactly one lens is active at a time — replace semantics, never
   composed: lens.type is 'none' | 'focus' (node id). The preset lenses (owner / criticality /
   property chips) were cut 2026-08-18 (KG ruling: nothing outside the page's own toolbar ever
   emitted them). Every keep-set reads the Python-precomputed island id-lists; this file
   derives no edges and re-maps no vocabulary. */
var data = JSON.parse(document.getElementById('data-graph').textContent);
var byId = Object.create(null);
data.nodes.forEach(function (n) { byId[n.id] = n; });
var facts = document.getElementById('facts');
var lens = {type: 'none', value: null};
var groupSub0 = null;  // the server-rendered group subtitle, captured before the first rewrite

function el(tag, cls, text) {
  var e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== null && text !== undefined) e.textContent = String(text);
  return e;
}

function press(type, value, text) {
  var b = el('button', 'linklike', text);
  b.type = 'button'; b.dataset.lens = type; b.dataset.value = value;
  return b;
}

function parts(n) {  // [primary, secondary] — mirrors the node card's name/sub pair
  if (n.kind === 'process') return [n.activity || n.id, n.dept];
  return [n.name || n.id, n.id];
}

function row(key, value) {
  if (value === null || value === undefined || value === '') return null;
  var tr = el('tr');
  tr.appendChild(el('td', null, key));
  var td = el('td');
  if (value.nodeType) td.appendChild(value); else td.textContent = String(value);
  tr.appendChild(td);
  return tr;
}

function table(rows) {
  var t = el('table');
  rows.forEach(function (r) { if (r) t.appendChild(r); });
  facts.appendChild(t);
}

function section(title, ids) {  // every entry presses through to focus(id)
  facts.appendChild(el('h3', null, title + ' (' + ids.length + ')'));
  if (!ids.length) { facts.appendChild(el('p', 'hint', 'None recorded.')); return; }
  var ul = el('ul');
  ids.forEach(function (id) {
    var p = parts(byId[id] || {id: id});          // id/dept first, then the long name:
    var li = el('li');                            // one scannable line per entry
    if (p[1] && p[1] !== p[0]) li.appendChild(el('span', 'mut', p[1] + ' ·'));
    var b = press('focus', id, p[0]);
    b.title = p[0];                               // CSS ellipsis never hides the full name
    li.appendChild(b);
    ul.appendChild(li);
  });
  facts.appendChild(ul);
}

function nodeFacts(n) {
  facts.appendChild(el('h2', null, parts(n)[0]));
  if (n.kind === 'asset') {
    var raw = n.criticality;
    var crit = (raw === null || raw === undefined || raw === '') ? 'unrated'
      : (String(raw) === n.crit ? n.crit : n.crit + ' (register: ' + raw + ')');
    // Register prose does not get a method term for a label. method.json defines MTPD as
    // one of six time horizons and ISO 22301 gives it to an ACTIVITY, not a resource; the
    // register's `mtpd` is a sentence on all 15 assets. The register's `rto` is asserted,
    // not derived from any grid, so it says so. Both stay under their real names on the
    // record's own activity, where the values genuinely are the method's.
    table([row('Asset id', n.id), row('Type', n.asset_type), row('Owner', n.owner_name || el('span', 'badge warn', 'OWNER MISSING')),
           row('Criticality', crit), row('RTO (stated)', n.rto), row('RPO', n.rpo),
           row('Impact of loss', n.mtpd),
           row('SPOF', n.spof ? el('span', 'badge bad', 'SPOF') : 'no'),
           row('Redundancy', n.redundancy), row('Supplier', n.supplier),
           row('Contract', n.contract_status),
           row('Quality flags', n.quality_flag_count || 0),
           row('PP4 issue', n.pp4_issue ? 'yes' : 'no')]);
    section('Depends on', n.chain || []);
    section('Dependents', n.impact || []);
    section('Dependent activities', n.procs || []);
  } else if (n.kind === 'process') {
    table([row('Department', n.dept), row('Activity', n.activity), row('Need', n.need),
           row('MTPD without this asset', n.consumer_mtpd)]);
    section('Depends on', n.feeds || []);  // depth 1: the assets feeding this process
  } else if (n.kind === 'process_group') {
    // Variant B's aggregate card: the drill-down home for the collapsed consumers.
    // `procs` is the same field an asset's Consumers section reads, so section() gives
    // every entry the standard press-through to that process's own facts.
    table([row('Dependent activities', (n.procs || []).length),
           row('Departments', n.dept_count)]);
    section('Dependent activities', n.procs || []);
  } else if (n.kind === 'activity') {
    table([row('Record id', n.id), row('Department', n.dept),
           row('Owner', n.owner_name || el('span', 'badge warn', 'OWNER MISSING')),
           row('Recovery target', n.recovery_target), row('MTPD', n.mtpd)]);
    section('Depends on', n.chain || []);
    // The register's own lines for this activity's department — the same operation
    // written once per resource it needs. A rollup, so it lives here and lights nothing
    // on the canvas: dept_acts is deliberately absent from keepSet's tuple.
    if (n.dept) section('Dependent activities in ' + n.dept, n.dept_acts || []);
  } else {
    facts.appendChild(el('p', 'hint', 'Not in the register — unmodeled dependency '
                                      + '(a BIA finding, not an error).'));
    section('Dependents', n.impact || []);
  }
  if ((n.acts || []).length) section('BIA run overlay', n.acts);
}

function fillFacts() {
  facts.textContent = '';
  if (lens.type === 'focus' && byId[lens.value]) { nodeFacts(byId[lens.value]); return; }
  facts.appendChild(el('h2', null, 'Facts'));
  facts.appendChild(el('p', 'hint', 'Select a node to see what it depends on and what '
    + 'depends on it.'));
}

function keepSet() {  // null = no lens, keep everything
  if (lens.type === 'none') return null;
  var keep = Object.create(null);
  var n = byId[lens.value];
  if (!n) return null;
  keep[n.id] = true;
  ['chain', 'impact', 'procs', 'acts', 'keep_extra'].forEach(function (k) {
    (n[k] || []).forEach(function (id) { keep[id] = true; });
  });
  return keep;
}

function applyLens() {
  var keep = keepSet();
  document.querySelectorAll('.node').forEach(function (e) {
    e.classList.toggle('dim', !!keep && !keep[e.dataset.id]);
    e.classList.toggle('focused', lens.type === 'focus' && e.dataset.id === lens.value);
  });
  document.querySelectorAll('.edge').forEach(function (e) {
    var on = !keep || !!(keep[e.dataset.src] && keep[e.dataset.dst]);
    e.classList.toggle('dim', !on);
    e.classList.toggle('hot', on && lens.type === 'focus');
  });
  // The group card stands in for the whole consumer band, so it must answer the click too:
  // a fixed "N departments · select for the list" made every selection look identical (KG's
  // second live review). Counted off the same keep set that dims, never a second walk.
  // Capture-before-rewrite is safe on a deep-linked page: the first applyLens() reads the
  // server-rendered line before anything has overwritten it.
  var gsub = document.querySelector('[data-id="group:processes"] text.sub');
  if (gsub) {
    if (groupSub0 === null) groupSub0 = gsub.textContent;
    var grp = byId['group:processes'];
    // The group's own focus says nothing about a single selection's consumers, so it
    // restores the server-rendered line.
    // The count line is a noun phrase on purpose (KG's copy ruling, 2026-07-31): "1 of 33
    // serve" is ungrammatical and m === 1 is common — every single-consumer asset, and every
    // focused process, which keeps only itself. No verb, so one string reads right at any m.
    if (grp && keep && lens.type === 'focus' && lens.value !== 'group:processes') {
      var m = (grp.procs || []).filter(function (id) { return keep[id]; }).length;
      gsub.textContent = m + ' of ' + (grp.procs || []).length + ' in this selection';
    } else {
      gsub.textContent = groupSub0;
    }
  }
  // The fold (variant B): a kept folded card unfolds the consumer band, and the band
  // repacks around whatever survived. Derived from the same keep set that dims —
  // presentation state, never a second engine. The body class flips the svg's CSS
  // aspect and repack() rewrites it, so the view re-fits before the reveal pans.
  // Filtered off FOLDED, never off a fresh query: FOLDED is in the band's own slot order
  // (dept-sorted), so the survivors stack in the order the band promises.
  var open = FOLDED.filter(function (f) { return !!(keep && keep[f.el.dataset.id]); })
                   .map(function (f) { return f.el; });
  var wasUnfolded = document.body.classList.contains('unfolded');
  document.body.classList.toggle('unfolded', open.length > 0);
  var refit = repack(open);
  if (refit || (open.length > 0) !== wasUnfolded) resetView();
  revealFocused();  // the lens layer's reach into the view: reveal, plus re-fit on fold
  fillFacts();
}

function writeHash() {
  var h = lens.type === 'focus' ? '#' + encodeURIComponent(lens.value) : '';
  // file:// documents have an opaque origin and reject replaceState — lens still applies.
  try { history.replaceState(null, '', h || location.pathname + location.search); }
  catch (err) { /* deep links keep working over http */ }
}

function setLens(type, value) {
  lens = {type: type, value: value === undefined ? null : value};
  applyLens();
  writeHash();
}

function readHash() {
  var raw = (location.hash || '').slice(1);
  var s = raw;
  try { s = decodeURIComponent(raw); } catch (err) { s = raw; }
  // A stale link (an id that left the register) degrades to the base view, never to an
  // empty panel behind a dimmed canvas: membership is checked against the island.
  if (byId[s]) return {type: 'focus', value: s};
  return {type: 'none', value: null};
}

function pulseFocused() {  // deep-link arrival: point at the node the hash landed on
  var prev = document.querySelector('.node.pulse');
  // the reflow is what lets the same node pulse again on a repeat hash load
  if (prev) { prev.classList.remove('pulse'); void prev.getBoundingClientRect(); }
  var e = document.querySelector('.node.focused');
  if (e) e.classList.add('pulse');
}

/* Pan/zoom (D4) — direct manipulation of the svg viewBox. Nothing below touches the lens,
   and the lens layer's reach in here is revealFocused() plus a resetView() re-fit when
   the fold state changes the element's aspect. #canvas is overflow:hidden,
   so panning replaces canvas scrolling rather than doubling it. Scaling is always uniform, so the
   viewBox keeps its shape and the svg element never changes size or reflows the page. */
var svg = document.querySelector('#canvas svg');

/* The fold band's repack (KG 2026-07-31). Python authors the whole two-column band, and
   through 2026-07-31 a card kept its authored slot: a three-consumer selection then left
   two cards stranded rows apart while the canvas still reserved all 33 rows — "huge gaps
   ANY frontend designer would immediately flag". So the lens now picks WHICH authored
   slots get used, the group card's own column first, and re-fits the canvas to what is
   actually open. Python still owns every slot; this only chooses among them.
   It sits here because the aspect var it rewrites lives on the svg element above.
   The cost, paid deliberately: a moved card drags its edge, so route() is the one place
   this file owns geometry. It is _edge_svg's curve and the tests pin both sides. */
var FOLDED = [];                  // the band's cards, sorted into its own slot order
var BOX = Object.create(null);    // node id -> its box RIGHT NOW; only folded cards move
document.querySelectorAll('.node').forEach(function (e) {
  var r = e.querySelector('rect.box');
  if (!r) return;
  var b = {x: +r.getAttribute('x'), y: +r.getAttribute('y'),
           w: +r.getAttribute('width'), h: +r.getAttribute('height')};
  BOX[e.dataset.id] = b;
  if (e.classList.contains('folded')) FOLDED.push({el: e, home: b});
});
// Sorted by position, NOT left in document order: the cards ship in graph["nodes"] order
// while _geometry hands out slots in dept order, so slot i and card i are different cards
// and an unsorted repack scattered the survivors instead of stacking them. Column-major
// (x then y) is exactly the order _geometry filled the band in.
FOLDED.sort(function (a, b) { return a.home.x - b.home.x || a.home.y - b.home.y; });
var SLOTS = FOLDED.map(function (f) { return f.home; });
// The band's right-hand column IS the group card's column (pinned in test_dep_graph), so
// a selection that fits inside it stacks directly under the card it unfolded from, and
// never under the "BIA activities" header the band's left column inherits.
var BAND_X = SLOTS.length ? Math.max.apply(null, SLOTS.map(function (s) { return s.x; })) : 0;
var RIGHT = SLOTS.filter(function (s) { return s.x === BAND_X; });
// Python's two ratios carry every height number in play: --ar-folded is the floor (the
// asset block, which never moves) and --ar-unfolded less the full band's bottom is the
// gutter. Neither _MARGIN nor the row pitch is re-derived in here.
var AR = (svg.style.getPropertyValue('--ar-unfolded') || '/').trim().split('/');
var FLOOR_H = +(svg.style.getPropertyValue('--ar-folded') || '/').split('/')[1];
var PAD = +AR[1] - Math.max.apply(null, SLOTS.map(function (s) { return s.y + s.h; }));

function route(a, b) {  // mirrors _edge_svg: same three branches, same control offsets
  var ya = a.y + Math.floor(a.h / 2), yb = b.y + Math.floor(b.h / 2);
  if (b.x < a.x)
    return 'M' + a.x + ',' + ya + ' C' + (a.x - 60) + ',' + ya
         + ' ' + (b.x + b.w + 60) + ',' + yb + ' ' + (b.x + b.w) + ',' + yb;
  if (b.x > a.x)
    return 'M' + (a.x + a.w) + ',' + ya + ' C' + (a.x + a.w + 60) + ',' + ya
         + ' ' + (b.x - 60) + ',' + yb + ' ' + b.x + ',' + yb;
  return 'M' + (a.x + a.w) + ',' + ya + ' C' + (a.x + a.w + 70) + ',' + ya
       + ' ' + (b.x + b.w + 70) + ',' + yb + ' ' + (b.x + b.w) + ',' + yb;
}

function repack(open) {  // -> true when the aspect changed and the view has to re-fit
  if (!SLOTS.length) return false;
  var slots = open.length <= RIGHT.length ? RIGHT : SLOTS;
  FOLDED.forEach(function (f) {  // everything goes home first, so BOX never lies
    f.el.removeAttribute('transform');
    BOX[f.el.dataset.id] = f.home;
  });
  open.forEach(function (e, i) {
    var s = slots[i], home = BOX[e.dataset.id];
    if (s.x === home.x && s.y === home.y) return;
    e.setAttribute('transform',
                   'translate(' + (s.x - home.x) + ',' + (s.y - home.y) + ')');
    BOX[e.dataset.id] = {x: s.x, y: s.y, w: home.w, h: home.h};
  });
  // A card moved, so its edge has to be re-drawn from the live boxes — the baked `d`
  // still points at the slot it left. Only to-folded edges can have a moved endpoint;
  // the bundled fan ends at the group card, which is not folded and never moves.
  document.querySelectorAll('.edge.to-folded').forEach(function (p) {
    var a = BOX[p.dataset.src], b = BOX[p.dataset.dst];
    if (a && b) p.setAttribute('d', route(a, b));
  });
  var bottom = 0;
  open.forEach(function (e) {
    var b = BOX[e.dataset.id];
    if (b.y + b.h > bottom) bottom = b.y + b.h;
  });
  var want = AR[0] + '/' + Math.max(FLOOR_H, bottom + PAD);
  if (svg.style.getPropertyValue('--ar-unfolded').trim() === want) return false;
  svg.style.setProperty('--ar-unfolded', want);
  return true;
}

var fit, view;                   // fit = the home viewBox; view = what is on screen right now
var ptrs = Object.create(null);  // pointers that went down on the canvas: 1 = drag, 2 = pinch
var down = null;                 // where the current pointer went down, in client px
var grab = null;                 // the user-space point held under a dragging pointer
var spread = 0;                  // last two-pointer distance
var moved = false;               // travel passed 5 px — this gesture is a drag, not a click

/* The graph can never be dragged or zoomed out of its own window. Panning used to mutate
   view.x/view.y unbounded: from the fit view one ordinary 200px drag took view.y to -270 and
   the nodes fully in view from 20 to 7, i.e. the reader threw the graph off screen with a
   gesture they had every reason to try, and the only way back was a Reset view button
   nothing points them at (KG 2026-07-31, measured on the live page).

   `fit` is the content extent (see homeView), so it is also the pan boundary. Larger view
   than content = nothing to pan to, so centre it; smaller = keep the view rect inside the
   content rect. Living in draw() is the point: pan, zoomAt, resetView and revealFocused all
   already funnel through it, so this is one guard rather than four call sites that can each
   forget it. */
function clampView() {
  if (!fit) return;  // pre-boot draw: nothing to clamp against yet
  // Bounded by fit's RECT, not by 0,0 — homeView centres the content inside a grown viewBox,
  // so fit.x/fit.y are negative and hardcoding the origin would clamp that centring straight
  // back out and re-pin the graph to the top-left.
  view.x = view.w >= fit.w ? fit.x + (fit.w - view.w) / 2
                           : Math.min(Math.max(view.x, fit.x), fit.x + fit.w - view.w);
  view.y = view.h >= fit.h ? fit.y + (fit.h - view.h) / 2
                           : Math.min(Math.max(view.y, fit.y), fit.y + fit.h - view.h);
}

function draw() {
  clampView();
  svg.setAttribute('viewBox', view.x + ' ' + view.y + ' ' + view.w + ' ' + view.h);
}

function at(cx, cy) {  // client px → user units, exact under any preserveAspectRatio fit
  return new DOMPoint(cx, cy).matrixTransform(svg.getScreenCTM().inverse());
}

// The home view is the graph fitted to the canvas width, top-left anchored — the framing the
// page has always opened with, never magnified past 1:1. From there the 0.5x zoom floor brings
// the whole graph into view and the 4x ceiling reads any label.
function homeView() {  // the authored content extent, never the measured element box
  // The --ar-* pair is Python's own record of how tall the content actually is in each fold
  // state, so it IS the home view. Measuring the element instead (b.height/b.width) looked
  // equivalent and was not: `max-height` caps the element, and a capped box yielded a home
  // viewBox SHORTER than the content — 699 against a content bottom of 1044 at 2000x900 with
  // the band open, i.e. 345 units of graph outside the home view with no way to reach them,
  // because Reset view restores exactly this wrong rectangle (KG 2026-07-31, measured).
  // Letting the viewBox state the truth lets preserveAspectRatio letterbox the difference,
  // which shows less-large rather than less-graph.
  var w = svg.width.baseVal.value;
  var ar = (document.body.classList.contains('unfolded')
              ? svg.style.getPropertyValue('--ar-unfolded')
              : svg.style.getPropertyValue('--ar-folded')) || '';
  var parts = ar.split('/');
  var h = (parts.length === 2 && +parts[0] && +parts[1])
            ? w * (+parts[1]) / (+parts[0])
            : svg.height.baseVal.value;  // no fold band on this page: attribute aspect stands
  // Grow — never shrink — to the element's shape. The element no longer carries the graph's
  // aspect, so a viewBox left at the bare content extent would be letterboxed and the spare
  // height wasted exactly as before. Matching the element's ratio spends that height on
  // viewBox area instead, which is what a zoomed-in reader actually pans around in. Only ever
  // enlarging is the safety property: it can add empty margin, it can never crop the graph —
  // the failure mode of the earlier home-view fix, which cropped 345 units.
  var cw = w, ch = h;  // the content extent, before any growing
  var b = svg.getBoundingClientRect();
  if (b.width > 0 && b.height > 0) {
    h = Math.max(h, w * b.height / b.width);
    w = Math.max(w, h * b.width / b.height);
  }
  // Centre the content in whatever was grown. Top-aligning was tried and measured worse on
  // the thing this change exists to fix: zooming four notches at the pointer left 8 of 20
  // nodes on screen top-aligned versus 20 of 20 centred, at identical magnification (cards
  // 0.74x -> 1.30x either way). Centred, the grown margin absorbs the zoom instead of the
  // graph walking off the top edge. The cost is a margin above the graph at rest, which is
  // simply what a 4.14:1 graph looks like in a 1.7:1 window — the window is bigger than the
  // graph, and that is the point.
  // cw/ch ride along so zoomAt and clampView know where the CONTENT ends and margin begins.
  return {x: -(w - cw) / 2, y: -(h - ch) / 2, w: w, h: h, cw: cw, ch: ch};
}

function resetView() {  // recomputed rather than stored, so it re-fits after a window resize
  fit = homeView();
  view = {x: fit.x, y: fit.y, w: fit.w, h: fit.h};
  draw();
}

function zoomAt(cx, cy, k) {  // cursor-anchored: the point under (cx, cy) stays put
  var w = Math.min(Math.max(view.w * k, fit.w / 4), fit.w * 2);  // [0.5x, 4x] of the fit scale
  var p = at(cx, cy);
  // Never anchor a zoom on empty margin. The viewBox is grown to the window's shape, so on a
  // tall window most of it is blank space below the graph — and the pointer sits mid-window
  // by default. Anchoring out there walked the graph off the top edge on the first zoom.
  // Pulling the anchor back onto the content keeps the zoom centred on something real.
  if (fit.cw) p.x = Math.min(Math.max(p.x, 0), fit.cw);
  if (fit.ch) p.y = Math.min(Math.max(p.y, 0), fit.ch);
  k = w / view.w;                      // clamped, so the anchor math uses what we really did
  view.x = p.x - (p.x - view.x) * k;
  view.y = p.y - (p.y - view.y) * k;
  view.w = w;
  view.h *= k;
  draw();
}

svg.addEventListener('wheel', function (e) {
  // Plain wheel scrolls the PAGE, same as over any other element (KG 2026-07-31): the
  // canvas used to swallow every tick as a zoom, which reads as an ordinary scroll the
  // moment the cursor happens to be over the graph — and it sits higher on the page now
  // that the legend/footer fold moved out from above it, so it is the thing under the
  // cursor far more often. An unmodified scroll silently panned/zoomed away from the fit
  // view with no warning. Ctrl/Cmd+wheel still zooms: the modifier every mainstream
  // pan/zoom canvas already uses (Figma, Maps, VS Code's minimap), and the one Chrome
  // itself sets on the wheel event it synthesizes for a trackpad pinch, so pinch-to-zoom
  // keeps working — same gesture, same code path, no separate case to maintain.
  if (!(e.ctrlKey || e.metaKey)) return;
  e.preventDefault();  // only past the gate does the canvas own the gesture
  if (!e.deltaY) return;  // shift+wheel and sideways trackpad swipes are not a zoom-in
  zoomAt(e.clientX, e.clientY, e.deltaY > 0 ? 1.15 : 1 / 1.15);
}, {passive: false});

// Travel is measured on the document, not the svg, so a drag that starts on a control or in
// the facts panel suppresses its click too. Capture is taken only once a gesture is a real drag:
// a plain click never captures, so it still lands on the element it started on.
document.addEventListener('pointerdown', function (e) {
  down = {x: e.clientX, y: e.clientY};
  moved = false;
  if (!svg.contains(e.target)) return;  // a gesture off the canvas is gated, never panned
  ptrs[e.pointerId] = {x: e.clientX, y: e.clientY};
  grab = at(e.clientX, e.clientY);
  spread = 0;
});

document.addEventListener('pointermove', function (e) {
  if (!down || !e.buttons) return;
  var p = ptrs[e.pointerId];
  if (!moved) {
    if (Math.hypot(e.clientX - down.x, e.clientY - down.y) <= 5) return;  // still a click
    moved = true;
    if (p) { svg.setPointerCapture(e.pointerId); svg.classList.add('grabbing'); }
  }
  if (!p) return;  // gesture started off the canvas: gated the click, nothing to pan
  p.x = e.clientX;
  p.y = e.clientY;
  var ids = Object.keys(ptrs);
  if (ids.length > 1) {  // two pointers pinch about their midpoint; one pointer pans
    var a = ptrs[ids[0]], b = ptrs[ids[1]];
    var d = Math.hypot(a.x - b.x, a.y - b.y);
    if (spread && d) zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, spread / d);
    spread = d;
    return;
  }
  var u = at(e.clientX, e.clientY);  // hold the grabbed point under the pointer
  view.x -= u.x - grab.x;
  view.y -= u.y - grab.y;
  draw();
});

function endPointer(e) {
  delete ptrs[e.pointerId];
  spread = 0;
  var rest = Object.keys(ptrs)[0];
  if (rest) grab = at(ptrs[rest].x, ptrs[rest].y);  // pinch → one-finger drag, without a jump
  else svg.classList.remove('grabbing');
}
// On the document, not the svg: a gesture that stayed under the 5 px gate never captured,
// so a pointer lifted just past the svg edge delivered its pointerup somewhere else
// entirely — the stale ptrs entry then made the next touch look like a pinch.
['pointerup', 'pointercancel', 'lostpointercapture'].forEach(function (t) {
  document.addEventListener(t, endPointer);
});
resetView();  // boot: draw the home view

function revealFocused() {  // pan the least that brings the focused node fully into view
  var e = document.querySelector('.node.focused');
  var box = e && BOX[e.dataset.id];  // the repacked box — the card's own rect attributes
  if (!box) return;                  // still name the slot a repack has just moved it off
  var p = 24;  // the page's own gutter: a revealed card is never flush against an edge
  var x = box.x - p, y = box.y - p;
  var w = box.w + 2 * p, h = box.h + 2 * p;
  view.x = Math.min(Math.max(view.x, x + w - view.w), x);  // already framed = no move at all
  view.y = Math.min(Math.max(view.y, y + h - view.h), y);
  draw();
}

function onClick(ev) {  // one entry point for every click on the page
  if (moved) { moved = false; return; }  // the tail of a drag is not a click
  var t = ev.target;
  if (!t || !t.closest) return;
  if (t.closest('#reset-view')) { resetView(); return; }  // view control, never the lens
  var btn = t.closest('[data-lens]');
  if (btn) { setLens(btn.dataset.lens, btn.dataset.value); return; }
  var node = t.closest('.node');
  if (node) { setLens('focus', node.dataset.id); return; }
  // A background click clears, but the provenance band is chrome, not canvas — the same
  // reason #toolbar is exempt. Its <summary> is a control the reader is meant to press, and
  // #evidence now sits inside it; pressing either used to throw away the lens they had just
  // set. Exempting the band leaves the native <details> toggle untouched.
  if (!t.closest('#facts, #toolbar, details.provenance')) setLens('none');
}

document.addEventListener('click', onClick);
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') { setLens('none'); return; }
  if (e.key !== 'Enter' && e.key !== ' ') return;
  var node = e.target && e.target.closest ? e.target.closest('.node') : null;
  if (node) { e.preventDefault(); setLens('focus', node.dataset.id); }
});
window.addEventListener('hashchange', function () {
  lens = readHash();
  applyLens();
  pulseFocused();
});
lens = readHash();
applyLens();
pulseFocused();
