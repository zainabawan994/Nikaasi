'use strict';
/**
 * Signal-fusion severity rule (rules-v1). See docs/SEVERITY_RULE.md.
 *
 *   C (citizen, 0..1) = 1 - exp(-Σ w_i / 2)
 *        w_i = floodEvidence × recency(30 min) × photo bonus, one vote per reporter
 *   W (weather, 0..1) = 0.6 × alertLevel + 0.4 × min(1, rain_mm_hr / 50)
 *   T (traffic, 0..1) = clamp((recency-weighted peak TTI in last 20 min / baseline − 1) / 1.5)
 *
 *   score = 100 × (0.45 C + 0.25 W + 0.30 T)
 *         + 8 if two signals ≥ 0.4, +15 if all three  (corroboration)
 *         + 10 if any report says a person is at risk (hazard escalation, needs C ≥ 0.3)
 *
 *   Watch < 30 ≤ Moderate < 55 ≤ High < 75 ≤ Critical
 *   Confidence: count of independent signals ≥ 0.3 → 1 Low, 2 Medium, 3 High
 */

const CITIZEN_WINDOW = 30; // sim-minutes
const TRAFFIC_WINDOW = 20;
const ALERT_SCORE = { none: 0, yellow: 0.35, orange: 0.65, red: 0.9 };
const WEIGHTS = { citizen: 0.45, weather: 0.25, traffic: 0.30 };
const LEVELS = [
  { name: 'Watch', min: 0 },
  { name: 'Moderate', min: 30 },
  { name: 'High', min: 55 },
  { name: 'Critical', min: 75 },
];

function severityOf(score) {
  let lvl = LEVELS[0].name;
  LEVELS.forEach((l) => { if (score >= l.min) lvl = l.name; });
  return lvl;
}

function citizenSignal(reports, now) {
  const byReporter = new Map();
  const recent = reports.filter((r) => now - r.t <= CITIZEN_WINDOW && now >= r.t);
  recent.forEach((r) => {
    const recency = 1 - (now - r.t) / CITIZEN_WINDOW * 0.6; // older reports keep 40 %
    const w = r.ai.floodEvidence * recency * (r.hasPhoto ? 1.15 : 1);
    byReporter.set(r.reporterId, Math.max(byReporter.get(r.reporterId) || 0, Math.min(1, w)));
  });
  const sum = [...byReporter.values()].reduce((a, b) => a + b, 0);
  const hazards = [...new Set(recent.map((r) => r.ai.hazard).filter((h) => h !== 'none'))];
  return {
    score: +(1 - Math.exp(-sum / 2)).toFixed(3),
    count: recent.length,
    uniqueReporters: byReporter.size,
    withPhoto: recent.filter((r) => r.hasPhoto).length,
    peopleAtRisk: recent.some((r) => r.ai.peopleAtRisk),
    mentionsUnderpass: recent.some((r) => r.ai.mentionsUnderpass),
    hazards,
    firstAt: recent.length ? Math.min(...recent.map((r) => r.t)) : null,
  };
}

function weatherSignal(w) {
  if (!w) return { score: 0, alert: 'none', rainMmHr: 0 };
  const a = ALERT_SCORE[w.alert] ?? 0;
  const score = 0.6 * a + 0.4 * Math.min(1, (w.rainMmHr || 0) / 50);
  return { score: +score.toFixed(3), alert: w.alert, rainMmHr: +(w.rainMmHr || 0).toFixed(0), forecastMmHr: w.forecastMmHr, source: w.source };
}

function trafficSignal(history, baseline, now) {
  const win = (history || []).filter((h) => now - h.t <= TRAFFIC_WINDOW);
  if (!win.length || !baseline) return { score: 0, spike: 1, tti: 1, trend: 'flat' };
  // recency-weighted peak: a jam 20 min ago counts 20 %, one right now counts fully
  const eff = win.map((h) => ({ t: h.t, v: 1 + (h.tti - 1) * (1 - ((now - h.t) / TRAFFIC_WINDOW) * 0.8) }));
  const best = eff.reduce((a, b) => (b.v > a.v ? b : a));
  const spike = best.v / baseline;
  const first = win[0].tti, last = win[win.length - 1].tti;
  const trend = last > first * 1.15 ? 'rising' : last < first * 0.87 ? 'falling' : 'flat';
  const peakAt = best.t;
  return {
    score: +Math.max(0, Math.min(1, (spike - 1) / 1.5)).toFixed(3),
    spike: +spike.toFixed(2), tti: +last.toFixed(2), baseline: +baseline.toFixed(2), trend,
    minutesAgo: Math.round(now - peakAt),
  };
}

function assess({ sector, reports, weather, trafficHistory, trafficBaseline, now }) {
  const C = citizenSignal(reports, now);
  const W = weatherSignal(weather);
  const T = trafficSignal(trafficHistory, trafficBaseline, now);

  const base = 100 * (WEIGHTS.citizen * C.score + WEIGHTS.weather * W.score + WEIGHTS.traffic * T.score);
  const strong = [C.score, W.score, T.score].filter((s) => s >= 0.4).length;
  const corroboration = strong === 3 ? 15 : strong === 2 ? 8 : 0;
  const hazard = C.peopleAtRisk && C.score >= 0.3 ? 10 : 0;
  const score = Math.round(Math.min(100, base + corroboration + hazard));

  const active = [C.score, W.score, T.score].filter((s) => s >= 0.3).length;
  const confidence = active >= 3 ? 'High' : active === 2 ? 'Medium' : 'Low';
  const confidencePct = Math.round(Math.min(97, 30 + active * 18 + Math.min(C.uniqueReporters, 5) * 3 + (T.trend === 'rising' ? 4 : 0)));

  const factors = [];
  if (C.count) factors.push(`${C.uniqueReporters} citizen report${C.uniqueReporters === 1 ? '' : 's'}${C.withPhoto ? ` (${C.withPhoto} with photo)` : ''}`);
  if (W.alert !== 'none') factors.push(`active ${W.alert} rainfall alert (${W.rainMmHr} mm/h)`);
  else if (W.rainMmHr >= 10) factors.push(`${W.rainMmHr} mm/h rain, no alert`);
  if (T.spike >= 1.3) factors.push(`traffic spike ${T.spike}× baseline${T.minutesAgo != null ? ` (peak ${T.minutesAgo} min ago)` : ''}`);

  const missing = [];
  if (!C.count) missing.push('no citizen reports');
  if (W.alert === 'none' && W.rainMmHr < 10) missing.push('no rainfall alert');
  if (T.spike < 1.3) missing.push('traffic normal');

  const severity = severityOf(score);
  const hazardNote = C.peopleAtRisk ? ' A report indicates a person may be at risk.' : C.hazards.includes('vehicle_stranded') ? ' Vehicles reported stranded.' : '';
  const explanation = `${confidence} confidence: ${factors.length ? factors.join(' + ') : 'no signals'} in ${sector} within the last ${TRAFFIC_WINDOW}–${CITIZEN_WINDOW} minutes.${missing.length ? ` Not corroborated by: ${missing.join(', ')}.` : ''}${hazardNote}`;

  return {
    sector, score, severity, confidence, confidencePct, explanation,
    breakdown: {
      citizen: +(100 * WEIGHTS.citizen * C.score).toFixed(1),
      weather: +(100 * WEIGHTS.weather * W.score).toFixed(1),
      traffic: +(100 * WEIGHTS.traffic * T.score).toFixed(1),
      corroboration, hazard,
    },
    signals: { citizen: C, weather: W, traffic: T },
    engine: 'rules-v1',
  };
}

module.exports = { assess, severityOf, LEVELS, WEIGHTS, CITIZEN_WINDOW, TRAFFIC_WINDOW };
