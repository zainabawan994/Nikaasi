'use strict';
/**
 * End-to-end: signal intake → fusion → plan → execution → dashboard outcome.
 * Runs the engine headlessly (manual ticks) and the HTTP API on a random port.
 */
const test = require('node:test');
const assert = require('node:assert');
delete process.env.GEMINI_API_KEY; // deterministic, offline
const { Engine } = require('../src/engine');
const { createServer } = require('../server');

test('G-10 monsoon story: detect → plan → execute → measurable improvement → resolve', () => {
  const e = new Engine();
  e.playScenario(); e.stop();
  let inc;
  for (let i = 0; i < 14; i++) e.tick();
  inc = e.activeIncident('G-10');
  assert.ok(inc, 'incident opened for G-10');
  assert.equal(inc.severity, 'Critical');
  assert.equal(e.assessments['G-10'].final.confidence, 'High');
  assert.ok(inc.plan.actions.some((a) => a.type === 'reroute'), 'plan reroutes traffic');
  assert.ok(inc.plan.actions.some((a) => a.id === 'dispatch_drain'), 'plan dispatches drain team');
  assert.ok(inc.plan.actions.some((a) => a.id === 'dispatch_rescue'), 'person at risk → rescue dispatched');
  assert.ok(inc.plan.actions.some((a) => a.type === 'alert'), 'plan alerts residents');
  // F-8 single "thora sa pani" report must NOT open an incident
  assert.equal(e.activeIncident('F-8'), undefined);

  const before = { ...e.trafficNow.metrics };
  e.execute(inc.id, 'test');
  const after = e.trafficNow.metrics;
  assert.ok(after.closedLinks >= 1);
  assert.ok(after.exposurePerHr < before.exposurePerHr * 0.2, `exposure ${before.exposurePerHr} → ${after.exposurePerHr}`);
  assert.ok(after.avgTripMin < before.avgTripMin, 'average trip time improves');
  assert.equal(e.tickets.filter((t) => t.incidentId === inc.id).length, 2);
  assert.ok(e.broadcasts.some((b) => b.incidentId === inc.id && b.delivered > 1000));

  for (let i = 0; i < 90 && inc.status !== 'resolved'; i++) e.tick();
  assert.equal(inc.status, 'resolved', 'incident resolves after crews drain the water');
  assert.ok(e.depth['G-10'] < e.shadowDepth['G-10'], 'response beats the no-action counterfactual');
  assert.ok(e.tickets.every((t) => t.status === 'Resolved'));
  assert.equal(e.closed.size, 0, 'roads reopened');
  assert.ok(inc.trail.length >= 5, 'reasoning trail recorded');
});

test('HTTP API: report intake, authority auth, execute', async () => {
  const engine = new Engine();
  const server = createServer(engine);
  await new Promise((r) => server.listen(0, r));
  const base = `http://127.0.0.1:${server.address().port}`;
  const post = (p, body, pin) => fetch(base + p, { method: 'POST', headers: { 'content-type': 'application/json', ...(pin ? { 'x-authority-pin': pin } : {}) }, body: JSON.stringify(body) });
  try {
    let r = await post('/api/reports', { sector: 'G-10', text: 'Pani bhar gaya hai, gaari phans gayi hai', reporterId: 'u1' });
    assert.equal(r.status, 200);
    const j = await r.json();
    assert.equal(j.report.ai.hazard, 'vehicle_stranded');
    assert.equal(j.sectorStatus.severity, 'Watch', 'one report alone is not enough');

    r = await post('/api/reports', { sector: 'Z-99', text: 'pani' });
    assert.equal(r.status, 400);
    r = await post('/api/reports', { lat: 33.676, lng: 73.015, text: 'Road band hai', reporterId: 'u2' });
    assert.equal((await r.json()).report.sector, 'G-10', 'GPS snaps to sector');

    r = await post('/api/scenario', { action: 'step' });
    assert.equal(r.status, 401, 'scenario control needs PIN');

    // corroborate with weather override → incident
    r = await post('/api/weather', { sector: 'G-10', alert: 'red', rainMmHr: 60 }, '1122');
    assert.equal(r.status, 200);
    await post('/api/reports', { sector: 'G-10', text: 'Underpass mein pani, road band hai', reporterId: 'u3' });
    const st = await (await fetch(`${base}/api/state`, { headers: { 'x-authority-pin': '1122' } })).json();
    const inc = st.incidents.find((i) => i.sector === 'G-10');
    assert.ok(inc, 'incident opened once weather corroborates');
    r = await post(`/api/incidents/${inc.id}/execute`, {}, '1122');
    assert.equal(r.status, 200);
    const pub = await (await fetch(`${base}/api/alerts?sector=G-10`)).json();
    assert.ok(pub.length >= 1, 'residents of G-10 received an alert');
    const w = await (await fetch(`${base}/api/feeds/weather`)).json();
    assert.equal(w.sectors['G-10'].alert, 'red');
  } finally {
    engine.stop();
    server.close();
  }
});
