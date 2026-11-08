'use strict';
/**
 * Mesoscopic traffic model.
 *  - Directed links with BPR volume-delay: t = t0 * (1 + 0.15 (v/c)^4)
 *  - Incremental assignment (4 × 25 %) of an OD demand matrix over Dijkstra
 *  - Floodwater reduces *actual* link capacity; drivers who have not been
 *    warned/rerouted keep routing on their usual (unflooded) perception.
 */

const MAX_TTI = 15;          // gridlock cap on travel-time index
const IMPASSABLE_CM = 35;    // cars stall above this depth
const EXPOSURE_CM = 30;      // vehicles entering water deeper than this risk stalling / floating

function buildDemand(world) {
  const sec = Object.values(world.sectors);
  const od = [];
  sec.forEach((a) => sec.forEach((b) => {
    if (a.id === b.id) return;
    const d = Math.abs(a.i - b.i) + Math.abs(a.j - b.j);
    od.push({ o: a.markaz, d: b.markaz, flow: 75 / (1 + d) });
  }));
  // through traffic (evening rush)
  const through = [
    ['n0_2', 'n4_2', 2400], ['n4_2', 'n0_2', 2600], // Srinagar Hwy
    ['n0_1', 'n4_1', 500], ['n4_1', 'n0_1', 500],   // Nazimuddin Rd
    ['n3_0', 'n3_3', 800], ['n3_3', 'n3_0', 800],   // 9th Ave
    ['n1_0', 'n1_3', 350], ['n1_3', 'n1_0', 350],   // 11th Ave
  ];
  through.forEach(([o, d, flow]) => od.push({ o, d, flow }));
  return od;
}

function directedLinks(world) {
  const out = [];
  world.links.forEach((l) => {
    out.push({ key: `${l.id}>`, link: l, from: l.a, to: l.b });
    out.push({ key: `${l.id}<`, link: l, from: l.b, to: l.a });
  });
  return out;
}

function linkFloodFactor(link, depths) {
  // capacity multiplier from the deepest water on any bordering sector
  let d = 0;
  link.sectors.forEach((s) => { d = Math.max(d, depths[s] || 0); });
  if (link.underpass) d *= 2.2; // underpasses pool water
  if (link.kind === 'local') d *= 1.2;
  return { depth: d, factor: d < 5 ? 1 : Math.max(0.06, 1 - d / 45) };
}

function bpr(t0, v, c) {
  const x = c > 0 ? v / c : 99;
  return t0 * Math.min(MAX_TTI, 1 + 0.15 * Math.pow(x, 4));
}

/**
 * @param world
 * @param depths   {sectorId: cm} actual water depth
 * @param closed   Set of link ids closed by traffic police
 * @param informed Set of sectors whose flooding drivers know about (alerts/diversions)
 */
function simulate(world, depths, closed = new Set(), informed = new Set()) {
  const dl = directedLinks(world);
  const adj = {};
  dl.forEach((e) => {
    const l = e.link;
    const t0 = (l.km / l.speed) * 60; // minutes
    const fl = linkFloodFactor(l, depths);
    e.t0 = t0;
    e.depth = fl.depth;
    e.capActual = l.cap * fl.factor;
    const knows = l.sectors.some((s) => informed.has(s));
    e.capPerceived = knows ? e.capActual : l.cap;
    e.closed = closed.has(l.id);
    e.flow = 0;
    (adj[e.from] = adj[e.from] || []).push(e);
  });

  // residents who received an alert for a flooded sector largely stay put
  const suppressed = new Set([...informed].filter((s) => (depths[s] || 0) >= 15).map((s) => world.sectors[s].markaz));
  const demand = buildDemand(world).map((q) => (suppressed.has(q.o) || suppressed.has(q.d) ? { ...q, flow: q.flow * 0.3 } : q));
  const STEPS = 4;
  let unroutable = 0;
  for (let s = 0; s < STEPS; s++) {
    // perceived costs for this increment
    dl.forEach((e) => {
      e.cost = e.closed ? Infinity : bpr(e.t0, e.flow, e.capPerceived);
      if (!e.closed && e.capPerceived < e.link.cap && e.depth >= IMPASSABLE_CM) e.cost = e.t0 * 40; // known-impassable
    });
    // group by origin for one Dijkstra per origin
    const byOrigin = {};
    demand.forEach((q) => { (byOrigin[q.o] = byOrigin[q.o] || []).push(q); });
    Object.entries(byOrigin).forEach(([o, qs]) => {
      const { prev } = dijkstra(world.nodes, adj, o);
      qs.forEach((q) => {
        let n = q.d; const path = [];
        while (n !== o) { const e = prev[n]; if (!e) { path.length = 0; break; } path.push(e); n = e.from; }
        if (!path.length) { unroutable += q.flow / STEPS; return; }
        path.forEach((e) => { e.flow += q.flow / STEPS; });
      });
    });
  }

  // actual performance
  let vehMin = 0, exposure = 0, stalled = 0;
  const totalTrips = demand.reduce((a, q) => a + q.flow, 0);
  dl.forEach((e) => {
    e.tActual = e.closed ? 0 : bpr(e.t0, e.flow, e.capActual);
    if (!e.closed && e.depth >= IMPASSABLE_CM && e.flow > 0) { e.tActual = e.t0 * MAX_TTI; stalled += e.flow * 0.04; }
    vehMin += e.flow * e.tActual;
    if (e.depth >= EXPOSURE_CM) exposure += e.flow;
  });

  // aggregate to undirected links
  const links = {};
  world.links.forEach((l) => { links[l.id] = { id: l.id, flow: 0, vc: 0, tti: 1, depth: 0, closed: closed.has(l.id) }; });
  dl.forEach((e) => {
    const L = links[e.link.id];
    L.flow += e.flow;
    L.vc = Math.max(L.vc, e.capActual > 0 ? e.flow / e.capActual : 0);
    L.tti = Math.max(L.tti, e.closed ? 1 : e.tActual / e.t0);
    L.depth = Math.max(L.depth, e.depth);
  });
  Object.values(links).forEach((L) => { L.flow = Math.round(L.flow); L.vc = +L.vc.toFixed(2); L.tti = +L.tti.toFixed(2); L.depth = +L.depth.toFixed(1); });

  // sector travel-time index = mean TTI of bordering arterials + local streets
  const sectors = {};
  Object.values(world.sectors).forEach((s) => {
    const ls = world.links.filter((l) => l.sectors.includes(s.id) && !closed.has(l.id));
    // flow-weighted so a stalled but emptied street counts less than a jammed highway
    const wsum = ls.reduce((a, l) => a + links[l.id].flow + 100, 0);
    const tti = ls.length ? ls.reduce((a, l) => a + links[l.id].tti * (links[l.id].flow + 100), 0) / wsum : 1;
    const vc = ls.length ? ls.reduce((a, l) => a + links[l.id].vc, 0) / ls.length : 0;
    sectors[s.id] = { tti: +tti.toFixed(2), vc: +vc.toFixed(2) };
  });

  return {
    links, sectors,
    metrics: {
      avgTripMin: +(vehMin / totalTrips).toFixed(1),
      vehHours: Math.round(vehMin / 60),
      exposurePerHr: Math.round(exposure),
      stalledVehicles: Math.round(stalled),
      unroutable: Math.round(unroutable),
      closedLinks: closed.size,
    },
  };
}

function dijkstra(nodes, adj, src) {
  const dist = {}, prev = {}, done = new Set();
  Object.keys(nodes).forEach((n) => { dist[n] = Infinity; });
  dist[src] = 0;
  const ids = Object.keys(nodes);
  for (;;) {
    let u = null, best = Infinity;
    for (const n of ids) if (!done.has(n) && dist[n] < best) { best = dist[n]; u = n; }
    if (u === null) break;
    done.add(u);
    for (const e of adj[u] || []) {
      if (!isFinite(e.cost)) continue;
      const nd = dist[u] + e.cost;
      if (nd < dist[e.to]) { dist[e.to] = nd; prev[e.to] = e; }
    }
  }
  return { dist, prev };
}

/** Travel time (min) from a node to a sector's markaz on the current network (for dispatch ETAs). */
function travelTime(world, depths, closed, fromNode, sectorId) {
  const dl = directedLinks(world);
  const adj = {};
  dl.forEach((e) => {
    const fl = linkFloodFactor(e.link, depths);
    const t0 = (e.link.km / e.link.speed) * 60;
    // emergency vehicles use closed roads but are slowed by water
    e.cost = fl.depth >= IMPASSABLE_CM * 1.5 ? Infinity : t0 * (1 + fl.depth / 15) * (closed.has(e.link.id) ? 1.2 : 1);
    (adj[e.from] = adj[e.from] || []).push(e);
  });
  const { dist, prev } = dijkstra(world.nodes, adj, fromNode);
  const target = world.sectors[sectorId].markaz;
  const path = [];
  let n = target;
  while (n !== fromNode && prev[n]) { path.unshift(prev[n].link.id); n = prev[n].from; }
  return { minutes: dist[target], path };
}

module.exports = { simulate, travelTime, IMPASSABLE_CM, EXPOSURE_CM };
