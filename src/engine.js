'use strict';
/**
 * Nikaasi engine: owns the simulated world clock and runs, every simulated minute,
 *   1. scenario script  → weather "truth" (storm)            [mock PMD feed]
 *   2. hydrology        → hidden ground-truth water depth
 *   3. traffic model    → congestion per road / sector         [mock traffic feed]
 *   4. signal fusion    → per-sector severity + confidence     [rules-v1 (+ Gemini)]
 *   5. incident manager → open / escalate / plan / auto-execute
 *   6. response sim     → tickets progress, crews pump water, closures, alerts
 */
const EventEmitter = require('events');
const { buildWorld } = require('./config');
const traffic = require('./traffic');
const fusion = require('./fusion');
const planner = require('./planner');
const nlp = require('./nlp');
const gemini = require('./gemini');
const { G10_MONSOON } = require('./scenario');

const START_CLOCK_MIN = 18 * 60 + 40; // 18:40 PKT
const PUMP_RATE = 5;                 // cm/min removed by a drain crew on site
const MAX_DEPTH = 60;

class Engine extends EventEmitter {
  constructor({ tickMs = 1500, seed = 7 } = {}) {
    super();
    this.world = buildWorld();
    this.baseTickMs = tickMs;
    this.seed = seed;
    this.reset();
  }

  // ---------------------------------------------------------------- lifecycle
  reset() {
    this.stop();
    this.rng = mulberry32(this.seed);
    this.simMin = 0;
    this.speed = 1;
    this.running = false;
    this.scenario = null;
    this.storm = { center: 'G-10', peak: 0, alerts: {} };
    this.manualWeather = {};
    this.depth = {}; this.shadowDepth = {}; this.pumping = {}; this.drainFixed = {};
    Object.keys(this.world.sectors).forEach((s) => { this.depth[s] = 0; this.shadowDepth[s] = 0; this.pumping[s] = 0; this.drainFixed[s] = 0; });
    this.reports = [];
    this.photos = new Map();
    this.incidents = [];
    this.tickets = [];
    this.broadcasts = [];
    this.events = [];
    this.closed = new Set();
    this.informed = new Set();
    this.settings = { autoExecute: false };
    this.aiCache = {}; // sector -> {result, simMin, pending, lastCallMs}
    this.weather = {};
    this.baseline = traffic.simulate(this.world, {});
    this.trafficNow = this.baseline;
    this.shadow = null;
    this.trafficHistory = {};
    Object.keys(this.world.sectors).forEach((s) => { this.trafficHistory[s] = []; });
    this.assessments = {};
    this.seq = { inc: 0, ticket: 1040, rep: 0, bc: 0 };
    this.updateWeather();
    this.recordTraffic();
    this.fuseAll();
    this.log('system', 'Nikaasi online. Feeds connected: citizen reports, PMD weather (mock), ITP traffic (mock).');
    this.emit('state');
  }

  start() {
    if (this.timer) return;
    this.running = true;
    this.timer = setInterval(() => this.tick(), Math.max(150, this.baseTickMs / this.speed));
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.running = false;
  }

  setSpeed(x) {
    this.speed = Math.max(0.5, Math.min(8, Number(x) || 1));
    if (this.timer) { this.stop(); this.start(); }
  }

  playScenario() {
    this.reset();
    this.scenario = { name: 'G-10 monsoon evening', startedAt: this.simMin, script: G10_MONSOON.map((s) => ({ ...s, done: false })) };
    this.log('scenario', 'Scenario started: "G-10 monsoon evening".');
    this.start();
    this.tick();
  }

  // ---------------------------------------------------------------- main tick
  tick() {
    this.simMin += 1;
    this.runScript();
    this.updateWeather();
    this.updateHydrology();
    this.trafficNow = traffic.simulate(this.world, this.depth, this.closed, this.informed);
    this.shadow = this.incidents.some((i) => i.execution)
      ? traffic.simulate(this.world, this.shadowDepth) : null;
    this.recordTraffic();
    this.fuseAll();
    this.manageIncidents();
    this.progressTickets();
    this.emit('state');
  }

  runScript() {
    if (!this.scenario) return;
    const rel = this.simMin - this.scenario.startedAt;
    this.scenario.script.forEach((step) => {
      if (step.done || step.at > rel) return;
      step.done = true;
      if (step.storm) this.storm = step.storm;
      if (step.log) this.log('weather', step.log);
      if (step.report) this.submitReport({ ...step.report, bot: true }).catch(() => {});
    });
  }

  // ---------------------------------------------------------------- mock feeds
  updateWeather() {
    const c = this.world.sectors[this.storm.center];
    const w = {};
    Object.values(this.world.sectors).forEach((s) => {
      const d = Math.hypot(s.i - c.i, s.j - c.j);
      const jitter = 1 + (this.rng() - 0.5) * 0.12;
      let rain = this.storm.peak * Math.exp(-(d * d) / (2 * 1.1 * 1.1)) * jitter;
      let alert = 'none';
      ['yellow', 'orange', 'red'].forEach((lvl) => { if ((this.storm.alerts[lvl] || []).includes(s.id)) alert = lvl; });
      const m = this.manualWeather[s.id];
      if (m) { alert = m.alert; rain = m.rainMmHr; }
      w[s.id] = {
        sector: s.id, alert, rainMmHr: +rain.toFixed(1),
        forecastMmHr: +(rain * (this.storm.peak > 30 ? 0.9 : 0.6)).toFixed(1),
        source: m ? 'Manual override (authority)' : 'PMD Islamabad (mock feed)',
        observedAt: this.clock(),
      };
    });
    this.weather = w;
  }

  updateHydrology() {
    Object.values(this.world.sectors).forEach((s) => {
      const inflow = (this.weather[s.id].rainMmHr / 60) * s.hydro.runoff * 4;
      const out = s.hydro.drain + this.drainFixed[s.id] + this.pumping[s.id];
      this.depth[s.id] = clamp(this.depth[s.id] + inflow - out, 0, MAX_DEPTH);
      this.shadowDepth[s.id] = clamp(this.shadowDepth[s.id] + inflow - s.hydro.drain, 0, MAX_DEPTH);
    });
  }

  recordTraffic() {
    Object.keys(this.world.sectors).forEach((s) => {
      const noise = 1 + (this.rng() - 0.5) * 0.06;
      const h = this.trafficHistory[s];
      h.push({ t: this.simMin, tti: +(this.trafficNow.sectors[s].tti * noise).toFixed(2) });
      while (h.length > 60) h.shift();
    });
  }

  /** What the (mock) traffic system publishes: per-road congestion, no water depth. */
  trafficFeed() {
    const links = {};
    Object.values(this.trafficNow.links).forEach((l) => { links[l.id] = { tti: l.tti, vc: l.vc, flow: l.flow, closed: l.closed }; });
    const sectors = {};
    Object.keys(this.world.sectors).forEach((s) => {
      const h = this.trafficHistory[s];
      sectors[s] = { tti: h[h.length - 1].tti, baseline: this.baseline.sectors[s].tti, history: h.slice(-30) };
    });
    return { source: 'ITP / navigation probe data (mock feed)', observedAt: this.clock(), sectors, links };
  }

  // ---------------------------------------------------------------- citizen reports
  async submitReport({ sector, reporterId, name, text, photo, lat, lng, x, y, bot = false }) {
    if (!sector || !this.world.sectors[sector]) throw httpError(400, 'Unknown sector');
    const desc = String(text || '').trim().slice(0, 600);
    if (desc.length < 3) throw httpError(400, 'Please describe what you see');
    const id = `R-${String(++this.seq.rep).padStart(4, '0')}`;
    const hasPhoto = typeof photo === 'string' && photo.startsWith('data:image/');
    if (hasPhoto) {
      if (photo.length > 2_000_000) throw httpError(413, 'Photo too large (max ~1.5 MB)');
      this.photos.set(id, photo);
    }
    let ai = nlp.interpret(desc, hasPhoto);
    if (gemini.enabled()) {
      try { ai = { ...(await gemini.interpretReport(desc, hasPhoto ? photo : null)), tags: ai.tags }; } catch (e) {
        ai.engineNote = `Gemini unavailable (${e.message.slice(0, 60)}), used offline lexicon`;
      }
    }
    const report = {
      id, sector, reporterId: String(reporterId || id).slice(0, 64), name: String(name || 'Resident').slice(0, 40),
      text: desc, hasPhoto, t: this.simMin, clock: this.clock(), ai, bot,
      position: Number.isFinite(lat) ? { lat, lng } : Number.isFinite(x) ? { x, y } : null,
    };
    this.reports.push(report);
    this.log('report', `${report.name} (${sector}): "${desc.slice(0, 80)}${desc.length > 80 ? '…' : ''}" → AI: ${ai.summary} [evidence ${ai.floodEvidence}]`, sector);
    // re-fuse immediately so the reporter sees the effect
    this.fuseAll();
    this.manageIncidents({ reason: `new report ${id}` });
    this.emit('state');
    const inc = this.activeIncident(sector);
    return { report, sectorStatus: this.publicAssessment(sector), incidentId: inc ? inc.id : null };
  }

  // ---------------------------------------------------------------- fusion
  fuseAll() {
    const feed = this.trafficFeed();
    Object.keys(this.world.sectors).forEach((s) => {
      const rules = fusion.assess({
        sector: s,
        reports: this.reports.filter((r) => r.sector === s),
        weather: this.weather[s],
        trafficHistory: this.trafficHistory[s],
        trafficBaseline: feed.sectors[s].baseline,
        now: this.simMin,
      });
      this.explainDiversion(s, rules);
      this.assessments[s] = this.combineWithAI(rules);
    });
  }

  /** Traffic displaced by our own diversions is not evidence of flooding next door. */
  explainDiversion(s, rules) {
    const near = this.incidents.filter((i) => i.status !== 'resolved' && i.sector !== s && planner.neighbours(this.world, i.sector).includes(s));
    const diverting = near.find((i) => i.plan && i.plan.actions.some((x) => x.type === 'reroute' && x.status === 'done'));
    const bigger = near.find((i) => planner.RANK[i.severity] >= 2 && (this.assessments[i.sector]?.final.score || 0) > rules.score);
    const src = diverting || bigger;
    if (!src || rules.signals.traffic.score === 0) return;
    const T = rules.signals.traffic;
    const removed = Math.round(100 * fusion.WEIGHTS.traffic * T.score * 0.6);
    T.score = +(T.score * 0.4).toFixed(3);
    T.note = diverting ? `partly explained by diversion from ${src.sector} (${src.id})` : `likely spill-over from ${src.sector} (${src.id}) on shared roads`;
    rules.breakdown.traffic = +(rules.breakdown.traffic - removed).toFixed(1);
    rules.score = Math.max(0, rules.score - removed);
    // corroboration may no longer hold
    const strong = [rules.signals.citizen.score, rules.signals.weather.score, T.score].filter((x) => x >= 0.4).length;
    const corr = strong === 3 ? 15 : strong === 2 ? 8 : 0;
    rules.score = Math.max(0, rules.score - (rules.breakdown.corroboration - corr));
    rules.breakdown.corroboration = corr;
    rules.severity = fusion.severityOf(rules.score);
    rules.explanation += ` Traffic spike ${T.note}; discounted.`;
  }

  combineWithAI(rules) {
    const cache = this.aiCache[rules.sector];
    const final = { score: rules.score, severity: rules.severity, confidence: rules.confidence, explanation: rules.explanation, engine: 'rules-v1' };
    if (cache && cache.result && this.simMin - cache.simMin <= 10) {
      const ai = cache.result;
      // guard-rail: AI may move the rule score by at most ±15
      const blended = Math.round((rules.score + ai.score) / 2);
      final.score = clamp(blended, rules.score - 15, rules.score + 15);
      final.severity = fusion.severityOf(final.score);
      final.confidence = ai.confidence;
      final.explanation = ai.explanation || rules.explanation;
      final.engine = `${ai.engine} + rules-v1`;
      final.ai = { ...ai, ageMin: this.simMin - cache.simMin };
    }
    return { ...rules, final };
  }

  requestAI(sector, reason) {
    if (!gemini.enabled()) return;
    const c = (this.aiCache[sector] = this.aiCache[sector] || {});
    if (c.pending || Date.now() - (c.lastCallMs || 0) < 15000) return;
    c.pending = true; c.lastCallMs = Date.now();
    const rules = this.assessments[sector];
    const sample = this.reports.filter((r) => r.sector === sector).slice(-5).map((r) => ({ text: r.text, ai: r.ai.summary, minutesAgo: this.simMin - r.t }));
    gemini.judgeIncident(rules, sample).then((result) => {
      c.result = result; c.simMin = this.simMin;
      const inc = this.activeIncident(sector);
      if (inc) this.trail(inc, 'ai', `Gemini judgment (${result.engine}): ${result.score}/100, ${result.confidence} confidence — ${result.explanation}`);
      this.fuseAll();
      this.emit('state');
    }).catch((e) => {
      c.error = e.message;
      this.log('system', `Gemini call failed for ${sector}: ${e.message.slice(0, 90)} (continuing with rules-v1)`);
    }).finally(() => { c.pending = false; });
  }

  // ---------------------------------------------------------------- incidents
  activeIncident(sector) { return this.incidents.find((i) => i.sector === sector && i.status !== 'resolved'); }

  manageIncidents({ reason } = {}) {
    Object.entries(this.assessments).forEach(([sector, a]) => {
      const sev = a.final.severity;
      let inc = this.activeIncident(sector);
      if (!inc && planner.RANK[sev] >= 1) {
        inc = {
          id: `NKS-${String(++this.seq.inc).padStart(4, '0')}`, sector, status: 'open',
          openedAt: this.simMin, openedClock: this.clock(), severity: sev, peakSeverity: sev,
          trail: [], plan: null, execution: null, quietMin: 0,
        };
        this.incidents.push(inc);
        this.log('incident', `Incident ${inc.id} opened for ${sector} — ${sev} (${a.final.score}/100).`, sector);
        this.trail(inc, 'open', `Opened: ${a.final.explanation}`, a);
        this.requestAI(sector, 'opened');
      }
      if (!inc) return;
      inc.assessment = a;
      const prevSev = inc.severity;
      if (sev !== prevSev) {
        const up = planner.RANK[sev] > planner.RANK[prevSev];
        // hysteresis: only de-escalate after 3 quiet minutes
        if (up || (inc.lastChange == null || this.simMin - inc.lastChange >= 3)) {
          inc.severity = sev; inc.lastChange = this.simMin;
          if (planner.RANK[sev] > planner.RANK[inc.peakSeverity]) inc.peakSeverity = sev;
          this.trail(inc, up ? 'escalate' : 'deescalate', `${up ? 'Escalated' : 'De-escalated'} ${prevSev} → ${sev} (${a.final.score}/100). ${a.final.explanation}`, a);
          this.log('incident', `${inc.id} ${up ? 'escalated' : 'de-escalated'} to ${sev}.`, sector);
          this.requestAI(sector, 'severity change');
        }
      } else if (reason && reason.startsWith('new report') && a.signals.citizen.count) {
        this.trail(inc, 'evidence', `New evidence (${reason}). Score now ${a.final.score}/100. ${a.final.explanation}`, a);
        this.requestAI(sector, reason);
      }

      // (re)plan whenever severity rises above what the current plan covers
      const escalated = !inc.plan || planner.RANK[inc.severity] > planner.RANK[inc.plan.severity];
      const refresh = inc.plan && inc.planPending && !inc.execution && planner.RANK[inc.severity] >= 1;
      if (refresh && !escalated) {
        // keep a not-yet-executed plan current with the latest observations (silent update)
        const p = planner.plan(this.world, { ...a, severity: inc.plan.severity }, this.trafficFeed().links, inc.plan);
        if (p) { p.actions.forEach((x) => { x.status = 'proposed'; }); p.version = inc.plan.version; p.severity = inc.plan.severity; inc.plan = p; }
      }
      if (planner.RANK[inc.severity] >= 1 && escalated) {
        const p = planner.plan(this.world, { ...a, severity: inc.severity }, this.trafficFeed().links, inc.plan);
        if (p) {
          const executedIds = inc.execution ? new Set(inc.execution.actionsDone) : new Set();
          p.actions.forEach((x) => { x.status = executedIds.has(x.id) && x.type !== 'reroute' && x.type !== 'alert' ? 'done' : 'proposed'; });
          inc.plan = p;
          inc.planPending = p.actions.some((x) => x.status === 'proposed');
          this.trail(inc, 'plan', `Response plan v${p.version} (${p.severity}): ${p.actions.map((x) => x.title).join('; ')}.`);
          if (this.settings.autoExecute || (inc.severity === 'Critical' && this.settings.autoExecuteCritical)) this.execute(inc.id, 'auto');
        }
      }

      // resolution check (observable only: score low, no active tickets)
      if (inc.status === 'responding') {
        const openTickets = this.tickets.filter((t) => t.incidentId === inc.id && t.status !== 'Resolved');
        inc.quietMin = a.final.score < 30 && !openTickets.length ? inc.quietMin + 1 : 0;
        if (inc.quietMin >= 3) this.resolve(inc);
      } else if (inc.status === 'open' && a.final.score < 15 && this.simMin - inc.openedAt > 10) {
        inc.status = 'resolved'; inc.resolvedAt = this.simMin;
        this.trail(inc, 'resolve', 'Closed without action: signals subsided below threshold.');
      }
    });
  }

  execute(incidentId, by = 'authority') {
    const inc = this.incidents.find((i) => i.id === incidentId);
    if (!inc) throw httpError(404, 'Incident not found');
    if (!inc.plan) throw httpError(409, 'No plan to execute yet');
    const todo = inc.plan.actions.filter((x) => x.status === 'proposed');
    if (!todo.length) throw httpError(409, 'Plan already executed');

    if (!inc.execution) {
      inc.execution = {
        executedAt: this.simMin, executedClock: this.clock(), by, actionsDone: [],
        before: {
          sectorTTI: this.trafficNow.sectors[inc.sector].tti,
          sectors: mapValues(this.trafficNow.sectors, (v) => v.tti),
          metrics: { ...this.trafficNow.metrics },
          links: snapshotLinks(this.trafficNow.links),
          score: inc.assessment.final.score,
          severity: inc.severity,
        },
      };
      // the counterfactual world starts from the same water level
      this.shadowDepth = { ...this.depth };
    }
    inc.status = 'responding';
    todo.forEach((x) => {
      if (x.type === 'reroute') {
        x.links.forEach((l) => this.closed.add(l));
        this.informed.add(inc.sector);
        this.log('traffic', `ITP diversion active: ${x.links.length} segment(s) closed around ${inc.sector}; traffic rerouted.`, inc.sector);
      }
      if (x.type === 'traffic_advisory') this.log('traffic', `Advisory sent to ITP for ${inc.sector}.`, inc.sector);
      if (x.type === 'dispatch') this.createTicket(inc, x);
      if (x.type === 'alert') this.sendBroadcast(inc, x);
      x.status = 'done';
      x.executedClock = this.clock();
      inc.execution.actionsDone.push(x.id);
    });
    inc.planPending = false;
    this.trail(inc, 'execute', `Plan v${inc.plan.version} executed by ${by}: ${todo.map((x) => x.title).join('; ')}.`);
    // re-run traffic immediately so the dashboard shows the effect this tick
    this.trafficNow = traffic.simulate(this.world, this.depth, this.closed, this.informed);
    this.shadow = traffic.simulate(this.world, this.shadowDepth);
    this.emit('state');
    return inc;
  }

  createTicket(inc, action) {
    const isRescue = action.id === 'dispatch_rescue';
    const t = {
      id: `${isRescue ? 'R1122' : 'CDA-DRN'}-${++this.seq.ticket}`,
      incidentId: inc.id, sector: inc.sector, team: action.team, priority: action.priority,
      kind: isRescue ? 'rescue' : 'drain', etaMin: action.etaMin, route: action.route,
      status: 'Created', createdAt: this.simMin,
      history: [{ status: 'Created', clock: this.clock() }],
    };
    this.tickets.push(t);
    this.log('dispatch', `Ticket ${t.id} (${t.priority}) created → ${t.team}, ETA ${t.etaMin} min.`, inc.sector);
  }

  progressTickets() {
    this.tickets.forEach((t) => {
      const age = this.simMin - t.createdAt;
      const set = (status, note) => {
        if (t.status === status) return;
        t.status = status; t.history.push({ status, clock: this.clock(), note });
        this.log('dispatch', `${t.id}: ${status}${note ? ` — ${note}` : ''}`, t.sector);
      };
      if (t.status === 'Created' && age >= 1) set('Assigned', `${t.team} acknowledged`);
      else if (t.status === 'Assigned' && age >= 2) set('En route');
      else if (t.status === 'En route' && age >= 2 + t.etaMin) {
        if (t.kind === 'drain') {
          this.pumping[t.sector] += PUMP_RATE;
          set('On site — pumping', `${Math.round(this.depth[t.sector])} cm water measured by crew; unblocking nullah inlets`);
        } else set('On site', 'Rescue team assisting');
      } else if (t.status.startsWith('On site')) {
        if (t.kind === 'drain' && this.depth[t.sector] < 4) {
          this.pumping[t.sector] = Math.max(0, this.pumping[t.sector] - PUMP_RATE);
          this.drainFixed[t.sector] = 1.8; // cleared inlets restore normal drainage
          set('Resolved', 'Inlets cleared, water drained, road declared safe');
          this.reopenRoads(t.incidentId);
        } else if (t.kind === 'rescue' && age >= 2 + t.etaMin + 6) set('Resolved', 'Motorcyclist assisted, no further casualties');
      }
    });
  }

  reopenRoads(incidentId) {
    const inc = this.incidents.find((i) => i.id === incidentId);
    const reroute = inc?.plan?.actions.find((x) => x.type === 'reroute');
    if (reroute) {
      reroute.links.forEach((l) => this.closed.delete(l));
      this.log('traffic', `Roads around ${inc.sector} reopened after crew confirmation.`, inc.sector);
      this.trail(inc, 'reopen', 'Crew confirmed road safe — closures lifted, normal routing restored.');
    }
  }

  resolve(inc) {
    inc.status = 'resolved'; inc.resolvedAt = this.simMin; inc.resolvedClock = this.clock();
    this.informed.delete(inc.sector);
    inc.execution.after = {
      sectorTTI: this.trafficNow.sectors[inc.sector].tti,
      metrics: { ...this.trafficNow.metrics },
      score: inc.assessment.final.score,
    };
    this.trail(inc, 'resolve', `Resolved after ${inc.resolvedAt - inc.openedAt} min. Score ${inc.execution.before.score} → ${inc.assessment.final.score}.`);
    this.log('incident', `${inc.id} resolved.`, inc.sector);
    const bc = {
      id: `BC-${++this.seq.bc}`, incidentId: inc.id, sectors: [inc.sector], clock: this.clock(), simMin: this.simMin, kind: 'all_clear',
      message: { en: `[Nikaasi ALL CLEAR] Water cleared in ${inc.sector}. Roads reopened. Drive carefully.`, ur: `[Nikaasi] ${inc.sector} mein pani nikal gaya hai, sarkein khul gayi hain.` },
      recipients: this.world.sectors[inc.sector].residents, delivered: this.world.sectors[inc.sector].residents, failed: 0,
    };
    this.broadcasts.push(bc);
  }

  sendBroadcast(inc, action) {
    const recipients = action.recipients;
    const failed = Math.round(recipients * (0.02 + this.rng() * 0.03));
    const bc = {
      id: `BC-${++this.seq.bc}`, incidentId: inc.id, sectors: action.sectors, clock: this.clock(), simMin: this.simMin, kind: 'warning',
      severity: inc.severity, message: action.message, recipients, delivered: recipients - failed, failed,
      channels: ['SMS', 'App push', 'WhatsApp (mock)'],
    };
    this.broadcasts.push(bc);
    action.sectors.forEach((s) => this.informed.add(s));
    this.log('alert', `Alert ${bc.id} sent to ${recipients.toLocaleString('en-US')} residents (${bc.delivered.toLocaleString('en-US')} delivered, ${failed} failed).`, inc.sector);
  }

  trail(inc, kind, text, a) {
    inc.trail.push({
      clock: this.clock(), simMin: this.simMin, kind, text,
      snapshot: a ? { score: a.final.score, severity: a.final.severity, confidence: a.final.confidence, breakdown: a.breakdown, engine: a.final.engine } : undefined,
    });
  }

  // ---------------------------------------------------------------- authority controls
  setWeatherOverride(sector, alert, rainMmHr) {
    if (!this.world.sectors[sector]) throw httpError(400, 'Unknown sector');
    if (alert === 'clear') { delete this.manualWeather[sector]; this.log('weather', `Manual weather override cleared for ${sector}.`, sector); } else {
      if (!['none', 'yellow', 'orange', 'red'].includes(alert)) throw httpError(400, 'Bad alert level');
      this.manualWeather[sector] = { alert, rainMmHr: clamp(Number(rainMmHr) || 0, 0, 150) };
      this.log('weather', `Manual weather override: ${sector} ${alert.toUpperCase()} ${this.manualWeather[sector].rainMmHr} mm/h.`, sector);
    }
    this.updateWeather();
    this.fuseAll();
    this.emit('state');
  }

  // ---------------------------------------------------------------- views
  clock(min = this.simMin) {
    const m = (START_CLOCK_MIN + min) % (24 * 60);
    return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
  }

  log(type, text, sector) {
    this.events.push({ clock: this.clock(), simMin: this.simMin, type, text, sector });
    if (this.events.length > 300) this.events.shift();
  }

  publicAssessment(s) {
    const a = this.assessments[s];
    return { sector: s, score: a.final.score, severity: a.final.severity, confidence: a.final.confidence, confidencePct: a.confidencePct, explanation: a.final.explanation, engine: a.final.engine, ai: a.final.ai, rules: { score: a.score, severity: a.severity, explanation: a.explanation }, breakdown: a.breakdown, signals: a.signals };
  }

  state({ authority = true } = {}) {
    const sectors = {};
    Object.keys(this.world.sectors).forEach((s) => { sectors[s] = this.publicAssessment(s); });
    const base = {
      clock: this.clock(), simMin: this.simMin, running: this.running, speed: this.speed,
      scenario: this.scenario ? this.scenario.name : null,
      ai: { enabled: gemini.enabled(), model: gemini.enabled() ? gemini.model() : null },
      settings: this.settings,
      sectors,
      weather: this.weather,
      traffic: { sectors: mapValues(this.trafficNow.sectors, (v, k) => ({ ...v, baseline: this.baseline.sectors[k].tti })), links: this.trafficNow.links, metrics: this.trafficNow.metrics, baselineMetrics: this.baseline.metrics },
      closed: [...this.closed],
      broadcasts: this.broadcasts.slice(-20),
    };
    if (!authority) return base;
    return {
      ...base,
      shadow: this.shadow ? { links: this.shadow.links, sectors: this.shadow.sectors, metrics: this.shadow.metrics } : null,
      truth: { depth: mapValues(this.depth, (v) => +v.toFixed(1)), shadowDepth: mapValues(this.shadowDepth, (v) => +v.toFixed(1)) },
      incidents: this.incidents.map((i) => ({ ...i, assessment: i.assessment ? this.publicAssessment(i.sector) : null })),
      tickets: this.tickets,
      trafficHistory: mapValues(this.trafficHistory, (h) => h.slice(-30).map((x) => x.tti)),
      reports: this.reports.slice(-60).map(({ position, ...r }) => r),
      events: this.events.slice(-80),
    };
  }
}

function snapshotLinks(links) {
  const o = {};
  Object.values(links).forEach((l) => { o[l.id] = { tti: l.tti, vc: l.vc, flow: l.flow, closed: l.closed, depth: l.depth }; });
  return o;
}
function mapValues(o, f) { const r = {}; Object.entries(o).forEach(([k, v]) => { r[k] = f(v, k); }); return r; }
function clamp(x, a, b) { return Math.max(a, Math.min(b, x)); }
function httpError(status, message) { const e = new Error(message); e.status = status; return e; }
function mulberry32(a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }

module.exports = { Engine };
