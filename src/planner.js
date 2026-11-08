'use strict';
/**
 * Response planner: turns a fused assessment into concrete, simulatable actions.
 * It only uses what the system can observe (reports + feeds), never the hidden
 * ground-truth water depth.
 */
const { simulate, travelTime } = require('./traffic');

const RANK = { Watch: 0, Moderate: 1, High: 2, Critical: 3 };

function estimateDepth(assessment) {
  const c = assessment.signals.citizen;
  const t = assessment.signals.traffic;
  // depth guess from hazards + traffic, used only for predictions
  let d = 8;
  if (c.hazards.includes('road_blocked')) d = 25;
  if (c.hazards.includes('vehicle_stranded') || c.hazards.includes('people_at_risk')) d = 38;
  if (t.spike >= 3) d = Math.max(d, 35); else if (t.spike >= 1.8) d = Math.max(d, 22);
  return d;
}

function affectedLinks(world, sector, observedLinks, assessment) {
  const s = world.sectors[sector];
  const ids = new Set();
  world.links.filter((l) => l.sectors.includes(sector)).forEach((l) => {
    const o = observedLinks[l.id];
    if (l.underpass && assessment.signals.citizen.mentionsUnderpass) ids.add(l.id);
    else if (o && o.tti >= 2.5 && l.kind === 'arterial') ids.add(l.id);
  });
  if (!ids.size) {
    // fall back to the worst arterial bordering the sector
    const worst = world.links.filter((l) => l.sectors.includes(s.id) && l.kind === 'arterial')
      .sort((a, b) => (observedLinks[b.id]?.tti || 1) - (observedLinks[a.id]?.tti || 1))[0];
    if (worst && (observedLinks[worst.id]?.tti || 1) > 1.5) ids.add(worst.id);
  }
  return [...ids];
}

function neighbours(world, sector) {
  const s = world.sectors[sector];
  return Object.values(world.sectors).filter((q) => Math.abs(q.i - s.i) + Math.abs(q.j - s.j) === 1).map((q) => q.id);
}

function chooseDepot(world, depots, kind, sector, depthsGuess) {
  return depots.filter((d) => d.kind === kind)
    .map((d) => ({ depot: d, ...travelTime(world, depthsGuess, new Set(), d.node, sector) }))
    .sort((a, b) => a.minutes - b.minutes)[0];
}

function plan(world, assessment, observedLinks, existingPlan) {
  const sector = assessment.sector;
  const sev = assessment.severity;
  const rank = RANK[sev];
  if (rank < 1) return null;
  const c = assessment.signals.citizen;
  const depthGuess = estimateDepth(assessment);
  const depthsGuess = { [sector]: depthGuess };
  const actions = [];
  const priority = rank >= 3 ? 'P1' : rank === 2 ? 'P2' : 'P3';

  // 1. traffic
  const close = rank >= 2 ? affectedLinks(world, sector, observedLinks, assessment) : [];
  if (close.length) {
    const names = close.map((id) => world.links.find((l) => l.id === id).name);
    actions.push({
      id: 'reroute', type: 'reroute', icon: 'route',
      title: `Close ${close.length} flooded segment${close.length > 1 ? 's' : ''} & reroute traffic`,
      detail: `Close ${[...new Set(names)].join(', ')}. ITP Patrol 12 to divert at adjacent junctions; navigation advisories push alternate routes.`,
      links: close,
    });
  } else {
    actions.push({
      id: 'advisory', type: 'traffic_advisory', icon: 'route',
      title: 'Traffic advisory to ITP',
      detail: `Ask Islamabad Traffic Police to monitor ${sector} approaches; no closure yet (severity ${sev}).`,
      links: [],
    });
  }

  // 2. drain team
  const drain = chooseDepot(world, world.depots, 'drain', sector, depthsGuess);
  actions.push({
    id: 'dispatch_drain', type: 'dispatch', icon: 'truck', priority,
    title: `Dispatch ${drain.depot.team}${rank >= 2 ? ' with suction pump' : ' (inspection)'}`,
    detail: `${priority} ticket to ${drain.depot.name}. ETA ≈ ${Math.round(drain.minutes)} min via ${drain.path.length} road segments. Task: clear blocked nullah inlets in ${sector}${c.mentionsUnderpass ? ' and pump out the underpass' : ''}.`,
    team: drain.depot.team, depot: drain.depot.id, etaMin: Math.round(drain.minutes), route: drain.path,
  });

  // 3. rescue
  if (c.peopleAtRisk || (rank >= 3 && c.hazards.includes('vehicle_stranded'))) {
    const r = chooseDepot(world, world.depots, 'rescue', sector, depthsGuess);
    actions.push({
      id: 'dispatch_rescue', type: 'dispatch', icon: 'medic', priority: 'P1',
      title: `Dispatch ${r.depot.team}`,
      detail: `Citizen reports indicate ${c.peopleAtRisk ? 'a person may be injured/at risk' : 'stranded vehicles'}. ETA ≈ ${Math.round(r.minutes)} min.`,
      team: r.depot.team, depot: r.depot.id, etaMin: Math.round(r.minutes), route: r.path,
    });
  }

  // 4. residents
  const alertSectors = rank >= 2 ? [sector, ...neighbours(world, sector)] : [sector];
  const recipients = alertSectors.reduce((a, s) => a + world.sectors[s].residents, 0);
  const closedNames = close.length ? [...new Set(close.map((id) => world.links.find((l) => l.id === id).name))].join(', ') : null;
  actions.push({
    id: 'alert', type: 'alert', icon: 'bell',
    title: `Alert ${recipients.toLocaleString('en-US')} residents in ${alertSectors.length} sector${alertSectors.length > 1 ? 's' : ''}`,
    detail: `SMS + app push to opted-in residents of ${alertSectors.join(', ')}.`,
    sectors: alertSectors, recipients,
    message: composeAlert(sector, sev, closedNames, c),
  });

  // predicted impact (based on *estimated* depth, not ground truth)
  const informed = new Set(rank >= 2 ? [sector] : []);
  const noAction = simulate(world, depthsGuess);
  const withAction = simulate(world, depthsGuess, new Set(close), informed);
  const prediction = {
    depthGuessCm: depthGuess,
    sectorTTI: { before: noAction.sectors[sector].tti, after: withAction.sectors[sector].tti },
    avgTripMin: { before: noAction.metrics.avgTripMin, after: withAction.metrics.avgTripMin },
    exposurePerHr: { before: noAction.metrics.exposurePerHr, after: withAction.metrics.exposurePerHr },
  };

  const version = existingPlan ? existingPlan.version + (existingPlan.severity !== sev ? 1 : 0) : 1;
  return { severity: sev, priority, actions, prediction, version, createdAt: Date.now() };
}

function composeAlert(sector, sev, closedNames, c) {
  const en = `[Nikaasi ${sev.toUpperCase()}] Flooding in ${sector}.${closedNames ? ` ${closedNames} closed — use alternate routes.` : ''} Avoid low roads${c.mentionsUnderpass ? ' & underpasses' : ''}, move vehicles to higher ground. Emergency: 1122.`;
  const ur = `[Nikaasi] ${sector} mein pani bhar gaya hai.${closedNames ? ` ${closedNames} band hai, doosra rasta lein.` : ''} Neechi sarkon${c.mentionsUnderpass ? ' aur underpass' : ''} se door rahein. Emergency: 1122.`;
  return { en, ur };
}

module.exports = { plan, RANK, neighbours };
