'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { assess } = require('../src/fusion');
const { interpret } = require('../src/nlp');

const rep = (id, text, t = 10, photo = false) => ({ id, reporterId: id, sector: 'G-10', t, hasPhoto: photo, ai: interpret(text, photo) });
const flat = (tti, n = 20) => Array.from({ length: n }, (_, i) => ({ t: i, tti }));

test('Roman Urdu reports are understood', () => {
  assert.ok(interpret('Pani bhar gaya hai').floodEvidence >= 0.8);
  assert.equal(interpret('Gaari phans gayi hai underpass ke paas').hazard, 'vehicle_stranded');
  assert.equal(interpret('Motorcyclist gir gaya, madad karo').peopleAtRisk, true);
  assert.equal(interpret('Road band hai').hazard, 'road_blocked');
  assert.ok(interpret('pani nahi hai, sab theek hai').floodEvidence < 0.3);
});

test('a single citizen report alone stays at Watch with Low confidence', () => {
  const a = assess({ sector: 'G-10', reports: [rep('a', 'Pani bhar gaya hai')], weather: { alert: 'none', rainMmHr: 0 }, trafficHistory: flat(1), trafficBaseline: 1, now: 12 });
  assert.equal(a.severity, 'Watch');
  assert.equal(a.confidence, 'Low');
});

test('weather + traffic without reports can reach Moderate+ but not High confidence', () => {
  const a = assess({ sector: 'G-10', reports: [], weather: { alert: 'red', rainMmHr: 60 }, trafficHistory: flat(2.8), trafficBaseline: 1, now: 19 });
  assert.ok(a.score >= 30);
  assert.equal(a.confidence, 'Medium');
});

test('three corroborating signals → High confidence, Critical, explanation cites all sources', () => {
  const reports = [
    rep('a', 'Pani bhar gaya hai ghutno tak'), rep('b', 'Gaari phans gayi hai'),
    rep('c', 'Road band hai underpass pe'), rep('d', 'Motorcyclist gir gaya, madad!'),
  ];
  const a = assess({ sector: 'G-10', reports, weather: { alert: 'red', rainMmHr: 62 }, trafficHistory: flat(3.5), trafficBaseline: 1, now: 15 });
  assert.equal(a.severity, 'Critical');
  assert.equal(a.confidence, 'High');
  assert.match(a.explanation, /4 citizen reports \+ active red rainfall alert .* \+ traffic spike/);
  assert.equal(a.breakdown.corroboration, 15);
  assert.equal(a.breakdown.hazard, 10);
});

test('duplicate reports from the same person count once', () => {
  const one = assess({ sector: 'G-10', reports: [rep('a', 'Pani bhar gaya hai')], weather: null, trafficHistory: [], trafficBaseline: 1, now: 12 });
  const spam = assess({ sector: 'G-10', reports: [1, 2, 3, 4, 5].map((i) => ({ ...rep('a', 'Pani bhar gaya hai'), id: `x${i}` })), weather: null, trafficHistory: [], trafficBaseline: 1, now: 12 });
  assert.equal(one.score, spam.score);
});

test('old reports expire from the 30-minute window', () => {
  const a = assess({ sector: 'G-10', reports: [rep('a', 'Pani bhar gaya hai', 0)], weather: null, trafficHistory: [], trafficBaseline: 1, now: 45 });
  assert.equal(a.signals.citizen.count, 0);
});
