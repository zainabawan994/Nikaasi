(function () {
  const $ = (id) => document.getElementById(id);
  const { esc, render, legendHTML, SEV_FILL } = window.NikaasiMap;
  const { icon } = window.NikaasiIcons;
  const RANK = { Watch: 0, Moderate: 1, High: 2, Critical: 3 };
  const STEPS = ['Created', 'Assigned', 'En route', 'On site', 'Resolved'];
  let world = null, state = null, es = null, me = null;
  let selSector = 'G-10', selIncident = null, userPicked = false, mapMode = 'current', view = 'overview';
  const hist = { active: [], rain: [], trip: [] };

  // static icons in the markup
  document.querySelectorAll('[data-ic]').forEach((el) => { el.outerHTML = icon(el.dataset.ic); });

  // ------------------------------------------------------------ session
  const toLogin = () => location.replace('/login.html?next=/authority.html');
  async function api(path, body) {
    const r = await fetch(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body || {}) });
    if (r.status === 401) { toLogin(); throw new Error('Signed out'); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `Request failed (${r.status})`);
    return j;
  }

  fetch('/api/auth/me').then(async (r) => {
    if (r.status === 401) return toLogin();
    if (!r.ok) throw new Error('no auth');
    me = await r.json();
    $('user-name').textContent = me.name;
    $('user-role').textContent = me.role;
    $('avatar').textContent = me.name.split(/\s+/).map((p) => p[0]).join('').slice(0, 2).toUpperCase();
    if (me.role === 'admin') $('admin-link').classList.remove('hidden');
    $('app').classList.remove('hidden');
    boot();
  }).catch(() => { document.body.innerHTML = '<div class="empty">This server does not support sign-in. Run the FastAPI backend.</div>'; });

  $('logout').onclick = async () => { await fetch('/api/auth/logout', { method: 'POST' }).catch(() => {}); location.replace('/login.html'); };

  async function boot() {
    const c = await fetch('/api/config').then((r) => r.json());
    world = c.world;
    $('legend').innerHTML = legendHTML();
    Object.keys(world.sectors).sort().forEach((s) => $('w-sector').insertAdjacentHTML('beforeend', `<option>${s}</option>`));
    $('w-sector').value = 'G-10';
    route();
    es = new EventSource('/api/stream');
    es.onmessage = (ev) => {
      state = JSON.parse(ev.data);
      if (!state.incidents) { es.close(); toLogin(); return; }
      record();
      draw();
    };
    es.onerror = () => { $('run-label').textContent = 'Reconnecting…'; $('live-dot').classList.add('off'); };
  }

  // ------------------------------------------------------------ routing (sidebar)
  function route() {
    view = (location.hash || '#overview').slice(1);
    if (!$(`view-${view}`)) view = 'overview';
    document.querySelectorAll('.view').forEach((v) => v.classList.toggle('hidden', v.id !== `view-${view}`));
    document.querySelectorAll('#nav a[data-view]').forEach((a) => a.classList.toggle('on', a.dataset.view === view));
    if (state) draw();
  }
  window.addEventListener('hashchange', route);

  // ------------------------------------------------------------ controls
  document.querySelectorAll('#mode-seg button').forEach((b) => b.addEventListener('click', () => {
    userPicked = false; selIncident = null;
    api('/api/scenario', { action: b.dataset.mode === 'live' ? 'live' : 'play' });
  }));
  document.querySelectorAll('#mapmode button').forEach((b) => b.addEventListener('click', () => { mapMode = b.dataset.m; drawOverviewMap(); }));
  $('pause').onclick = () => api('/api/scenario', { action: state && state.running ? 'pause' : 'resume' });
  $('play').onclick = () => { userPicked = false; selIncident = null; api('/api/scenario', { action: 'play' }); location.hash = '#overview'; };
  $('live-btn').onclick = () => { userPicked = false; selIncident = null; api('/api/scenario', { action: 'live' }); location.hash = '#overview'; };
  $('step').onclick = () => api('/api/scenario', { action: 'step' });
  $('reset').onclick = () => { selIncident = null; userPicked = false; api('/api/scenario', { action: 'reset' }); };
  $('speed').onchange = (e) => api('/api/scenario', { action: 'speed', speed: Number(e.target.value) });
  $('auto').onchange = (e) => api('/api/settings', { autoExecute: e.target.checked });
  $('show-depth').onchange = () => draw();
  $('w-apply').onclick = () => api('/api/weather', { sector: $('w-sector').value, alert: $('w-level').value, rainMmHr: Number($('w-rain').value) }).catch((e) => alert(e.message));

  // ------------------------------------------------------------ helpers
  const waiting = (i) => i.planPending && i.status !== 'resolved' && RANK[i.severity] >= 1;
  const sevBadge = (s, label) => `<span class="sev sev-${s}">${label || s}</span>`;
  const pct = (x) => `${Math.round(x * 100)}%`;
  const fmt = (n) => Number(n).toLocaleString('en-US');
  const active = () => state.incidents.filter((i) => i.status !== 'resolved').sort((a, b) => RANK[b.severity] - RANK[a.severity] || a.openedAt - b.openedAt);
  const incidentFor = (sector) => state.incidents.filter((i) => i.sector === sector).sort((a, b) => (a.status === 'resolved') - (b.status === 'resolved') || b.openedAt - a.openedAt)[0];
  const selected = () => state.incidents.find((i) => i.id === selIncident);
  const sectorSev = () => { const o = {}; Object.entries(state.sectors).forEach(([k, v]) => { o[k] = v.severity; }); return o; };
  const ticketsOf = (inc) => (inc ? state.tickets.filter((t) => t.incidentId === inc.id) : []);
  function keepScroll(el, html) { const t = el.scrollTop; el.innerHTML = html; el.scrollTop = t; }
  function place(inc) {
    if (!inc) return '';
    const underpass = state.reports.some((r) => r.sector === inc.sector && r.ai.mentionsUnderpass);
    return underpass ? `${inc.sector} Underpass` : `Sector ${inc.sector}`;
  }
  function spark(arr, color) {
    if (!arr || arr.length < 2) return '';
    const max = Math.max(...arr), min = Math.min(...arr), span = max - min || 1;
    const pts = arr.map((v, i) => [(i / (arr.length - 1)) * 100, 27 - ((v - min) / span) * 22]);
    const line = pts.map((p) => p.join(',')).join(' ');
    return `<svg viewBox="0 0 100 30" preserveAspectRatio="none"><polygon points="0,30 ${line} 100,30" fill="${color}" fill-opacity=".12"/><polyline points="${line}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
  }
  function record() {
    const push = (k, v) => { hist[k].push(v); if (hist[k].length > 40) hist[k].shift(); };
    push('active', active().length);
    push('rain', Math.max(...Object.values(state.weather).map((w) => w.rainMmHr)));
    push('trip', state.traffic.metrics.avgTripMin);
  }
  function congestionPct(sector, links) {
    const c = world.sectors[sector];
    const zone = new Set(Object.values(world.sectors).filter((q) => Math.abs(q.i - c.i) + Math.abs(q.j - c.j) <= 1).map((q) => q.id));
    const ls = world.links.filter((l) => l.kind === 'arterial' && l.sectors.some((s) => zone.has(s)) && links[l.id] && !links[l.id].closed);
    return ls.length ? Math.round((ls.filter((l) => links[l.id].tti >= 1.5).length / ls.length) * 100) : 0;
  }

  // ------------------------------------------------------------ draw
  function draw() {
    if (!state || !world) return;
    const live = state.mode === 'live';
    const now = new Date();
    $('clock').textContent = `${now.toLocaleDateString('en-GB', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' })} · ${state.clock}${live ? '' : ' (demo clock)'}`;
    $('run-label').textContent = !state.running ? 'Paused' : live ? 'Live system' : `Demo story · ${state.speed}×`;
    $('live-dot').classList.toggle('off', !state.running);
    document.querySelectorAll('#mode-seg button').forEach((b) => b.classList.toggle('on', (b.dataset.mode === 'live') === live));
    $('pause').innerHTML = icon(state.running ? 'pause' : 'play');
    $('speed').value = String(state.speed);
    $('speed').disabled = live; $('step').disabled = live;
    $('auto').checked = !!state.settings.autoExecute;

    const act = active();
    if (!userPicked && act.length) { selSector = act[0].sector; selIncident = act[0].id; }
    if (selIncident && !selected()) selIncident = null;
    if (!selIncident) { const i = incidentFor(selSector); selIncident = i ? i.id : null; }
    const pending = state.incidents.filter(waiting).length;
    $('bell-n').textContent = pending; $('bell-n').classList.toggle('hidden', !pending);
    $('nav-count').textContent = act.length; $('nav-count').classList.toggle('hidden', !act.length);

    if (view === 'overview') { drawKpis(act); drawOverviewMap(); drawIncidentCard(); drawActions(); drawImpact(); drawOvFeeds(); }
    if (view === 'incidents') { drawIncidentList(); drawDetail(); drawTrail(); }
    if (view === 'map') { drawBigMap(); drawSectorTable(); }
    if (view === 'dispatch') drawTickets();
    if (view === 'alerts') drawAlerts();
    if (view === 'feeds') { drawWeatherSource(live); drawLog(); }
  }

  function drawKpis(act) {
    const m = state.traffic.metrics, b = state.traffic.baselineMetrics, ws = state.weatherSource || {};
    const newer = state.incidents.filter((i) => state.simMin - i.openedAt <= 60).length;
    const wx = Object.values(state.weather).sort((x, y) => y.rainMmHr - x.rainMmHr)[0];
    const alertLvl = ['red', 'orange', 'yellow'].find((l) => Object.values(state.weather).some((w) => w.alert === l));
    const rainTitle = alertLvl === 'red' || alertLvl === 'orange' ? 'Heavy rain alert' : alertLvl === 'yellow' ? 'Rain watch' : wx.rainMmHr > 0 ? 'Light rain' : 'No rain';
    const ratio = m.avgTripMin / b.avgTripMin;
    const [tLabel, tCls] = ratio < 1.15 ? ['Normal', 'c-green'] : ratio < 1.5 ? ['Moderate', 'c-yellow'] : ratio < 2.5 ? ['High', 'c-orange'] : ['Severe', 'c-red'];
    const issues = [];
    if (state.mode === 'live' && !ws.ok) issues.push('rain feed');
    if (!state.db.enabled) issues.push('database');
    $('kpis').innerHTML = `
      <div class="card kpi"><div class="ico-tile blue">${icon('waves')}</div>
        <div class="value">${act.length}${newer ? `<span class="trend">+${newer} new</span>` : ''}</div><div class="label">Active incidents</div>
        <div class="spark">${spark(hist.active, '#2563EB')}</div></div>
      <div class="card kpi"><div class="ico-tile ${alertLvl === 'red' ? 'red' : 'blue'}">${icon('rain')}</div>
        <div class="value" style="font-size:17px">${rainTitle}</div><div class="label"><b>${wx.rainMmHr} mm/h</b> max${wx.rainMmHr > 0 ? ` in ${wx.sector}` : ''} · next hour ${wx.forecastMmHr} mm</div>
        <div class="spark">${spark(hist.rain, '#3B82F6')}</div></div>
      <div class="card kpi"><div class="ico-tile orange">${icon('car')}</div>
        <div class="value" style="font-size:17px">Traffic <span class="${tCls}">${tLabel}</span></div><div class="label">Avg trip <b>${m.avgTripMin} min</b> (normal ${b.avgTripMin}) · ${fmt(m.exposurePerHr)} veh/h into water</div>
        <div class="spark">${spark(hist.trip, '#EA580C')}</div></div>
      <div class="card kpi"><div class="ico-tile ${issues.length ? 'orange' : 'green'}">${icon('shield')}</div>
        <div class="value" style="font-size:17px">System <span class="${issues.length ? 'c-orange' : 'c-green'}">${issues.length ? 'Degraded' : 'Operational'}</span></div>
        <div class="label">${issues.length ? `Check: ${issues.join(', ')}` : `Rain feed ${state.mode === 'live' ? 'live' : 'demo'} · DB ${state.db.enabled ? 'on' : 'off'} · AI ${state.ai.enabled ? 'Gemini' : 'rules'}`}</div>
        <div class="spark">${spark([1, 1, 1, 1, 1, 1].map((v, i) => (issues.length && i > 3 ? 0.4 : v)), issues.length ? '#EA580C' : '#16A34A')}</div></div>`;
  }

  function mapIncidents() {
    return active().filter((i) => RANK[i.severity] >= 1).map((i) => ({ sector: i.sector, severity: i.severity, title: i.id === selIncident ? place(i) : '' }));
  }
  function onMapSelect(s) { userPicked = true; selSector = s; const i = incidentFor(s); selIncident = i ? i.id : null; draw(); }

  function drawOverviewMap() {
    const inc = selected();
    const ex = inc && inc.execution;
    document.querySelectorAll('#mapmode button').forEach((b) => {
      b.classList.toggle('on', b.dataset.m === mapMode);
      b.disabled = b.dataset.m !== 'current' && !ex;
    });
    if (mapMode !== 'current' && !ex) mapMode = 'current';
    let data, caption;
    if (mapMode === 'before') {
      data = { links: ex.before.links, sectorSev: { [inc.sector]: ex.before.severity }, incidents: [{ sector: inc.sector, severity: ex.before.severity, title: place(inc) }] };
      caption = `Before response · ${ex.executedClock}`;
    } else {
      data = { links: state.traffic.links, sectorSev: sectorSev(), depth: state.truth.depth, reports: state.reports.filter((r) => state.simMin - r.t <= 30), tickets: state.tickets, simMin: state.simMin, incidents: mapIncidents() };
      caption = mapMode === 'after' ? `After response · now ${state.clock}` : `Current conditions · ${state.clock}`;
    }
    $('map-caption').textContent = caption;
    render($('ov-map'), world, data, { theme: 'dark', selected: selSector, showDepth: true, showReports: mapMode !== 'before', showDepots: true, labels: true, onSelect: onMapSelect });
  }

  function drawIncidentCard() {
    const inc = selected();
    const el = $('inc-card');
    if (!inc || inc.status === 'resolved') {
      const top = Object.values(state.sectors).sort((a, b) => b.score - a.score).slice(0, 3);
      el.innerHTML = `<div class="card-h"><h3>${icon('shield')}Incident details</h3><div class="right"><span class="pill ok">All clear</span></div></div>
        <p class="inc-text">${inc ? `${esc(inc.id)} in ${esc(inc.sector)} has been resolved.` : 'No active flood incidents. Nikaasi is watching rainfall, traffic and citizen reports in every sector.'}</p>
        <h4 class="small muted" style="margin-bottom:8px">Highest sector scores</h4>
        <ul class="checks">${top.map((s) => `<li>${icon('pin', 'no')}<b style="min-width:44px">${s.sector}</b> ${s.score}/100 <span class="muted">· ${s.severity}</span></li>`).join('')}</ul>
        <div class="banner ok">${icon('check')}<b>Monitoring</b><span>${state.mode === 'live' ? 'Live rain from Open-Meteo, refreshed every 5 minutes.' : 'Demo story idle.'}</span></div>`;
      return;
    }
    const a = state.sectors[inc.sector], c = a.signals.citizen, w = a.signals.weather, t = a.signals.traffic;
    const r = 44, circ = 2 * Math.PI * r, conf = a.confidencePct;
    const confCol = a.confidence === 'High' ? '#DC2626' : a.confidence === 'Medium' ? '#EA580C' : '#64748B';
    const checks = [
      [c.uniqueReporters > 0, c.uniqueReporters > 0 ? `${c.uniqueReporters} citizen report${c.uniqueReporters > 1 ? 's' : ''}${c.withPhoto ? ` (${c.withPhoto} photo)` : ''}` : 'No citizen reports'],
      [w.score >= 0.3, w.score >= 0.3 ? `Heavy rain (${w.rainMmHr} mm/hr${w.alert !== 'none' ? `, ${w.alert} alert` : ''})` : `Rain ${w.rainMmHr} mm/hr`],
      [t.spike >= 1.3, t.spike >= 2 ? `Traffic ${t.spike}× slower than normal` : t.spike >= 1.3 ? `Traffic congestion +${Math.round((t.spike - 1) * 100)}%` : 'Traffic normal'],
    ];
    if (c.peopleAtRisk) checks.push([true, 'Person reported at risk']);
    const tk = ticketsOf(inc);
    const progress = tk.length ? tk.reduce((s, x) => s + (STEPS.findIndex((p) => x.status.startsWith(p)) + 1) / STEPS.length, 0) / tk.length : 0;
    let banner;
    if (waiting(inc)) {
      banner = `<div class="banner warn">${icon('bolt')}<b>Response plan ready</b><span>${inc.plan.actions.filter((x) => x.status === 'proposed').length} actions waiting for your approval</span></div>
        <button class="btn ${RANK[inc.plan.severity] >= 3 ? 'danger' : 'primary'}" id="execute" style="width:100%;margin-top:10px;padding:11px">${icon('check')}Approve &amp; execute ${inc.execution ? 'escalation' : 'plan'}</button>`;
    } else if (inc.status === 'responding') {
      const teams = tk.map((x) => x.team).join(' and ');
      banner = `<div class="banner red">${icon('bolt')}<b>Action in progress</b><span>Rerouting traffic${teams ? ` and dispatching ${esc(teams)}` : ''}.</span><div class="prog"><i style="width:${Math.round(progress * 100)}%"></i></div></div>`;
    } else {
      banner = `<div class="banner blue">${icon('clock')}<b>Monitoring</b><span>Severity below the response threshold.</span></div>`;
    }
    el.innerHTML = `<div class="card-h"><h3>${icon('flood')}Incident details</h3><div class="right">${sevBadge(inc.severity, `${inc.severity} severity`)}</div></div>
      <div class="inc-loc">${icon('pin')}${esc(place(inc))}, Islamabad</div>
      <p class="inc-text">${esc(a.explanation)}</p>
      <div class="inc-cols">
        <div><h4>Confidence level</h4><div class="ring"><svg viewBox="0 0 104 104"><circle cx="52" cy="52" r="${r}" fill="none" stroke="#EEF2F7" stroke-width="10"/><circle cx="52" cy="52" r="${r}" fill="none" stroke="${confCol}" stroke-width="10" stroke-linecap="round" stroke-dasharray="${(conf / 100) * circ} ${circ}"/></svg><b>${conf}%</b></div></div>
        <div><h4>AI reasoning</h4><ul class="checks">${checks.map(([ok, txt]) => `<li>${icon(ok ? 'check' : 'x', ok ? 'yes' : 'no')}${esc(txt)}</li>`).join('')}</ul>
          <div class="small muted" style="margin-top:8px">Score ${a.score}/100 · ${esc(a.engine)}</div></div>
      </div>${banner}`;
    const ex = $('execute');
    if (ex) ex.onclick = () => { ex.disabled = true; ex.textContent = 'Executing…'; api(`/api/incidents/${inc.id}/execute`).catch((e) => { alert(e.message); ex.disabled = false; }); };
  }

  function actionStatus(inc, x) {
    if (x.status === 'proposed') return ['pending', 'Pending'];
    if (x.type === 'dispatch') {
      const t = state.tickets.find((k) => k.incidentId === inc.id && k.team === x.team);
      if (t && t.status !== 'Resolved') return ['active', t.status];
      if (t) return ['done', `Resolved`];
    }
    return ['done', x.executedClock || 'Done'];
  }
  function drawActions() {
    const inc = selected();
    const el = $('actions-card');
    if (!inc || !inc.plan) {
      el.innerHTML = `<div class="card-h"><h3>${icon('bolt')}Response actions</h3></div><div class="empty">No response plan. A plan is drafted automatically when an incident reaches Moderate.</div>`;
      return;
    }
    const p = inc.plan;
    const label = inc.status === 'resolved' ? ['ok', 'Completed'] : waiting(inc) ? ['warn', 'Awaiting approval'] : ['blue', 'In progress'];
    const icons = { route: 'route', truck: 'truck', medic: 'medic', bell: 'megaphone' };
    const drain = ticketsOf(inc).find((t) => t.kind === 'drain');
    let eta = '';
    if (drain && drain.status !== 'Resolved') {
      const travel = ['Created', 'Assigned', 'En route'].includes(drain.status) ? Math.max(0, 2 + drain.etaMin - (state.simMin - drain.createdAt)) : 0;
      eta = `~${Math.max(1, Math.round(travel + (state.truth.depth[inc.sector] || 0) / 4))} min`;
    } else if (drain) eta = 'Cleared';
    el.innerHTML = `<div class="card-h"><h3>${icon('bolt')}Response actions</h3><div class="right"><span class="pill ${label[0]}">${label[1]}</span></div></div>
      <ol class="stepper">${p.actions.map((x) => {
        const [cls, when] = actionStatus(inc, x);
        return `<li class="${cls}"><span class="node">${cls === 'done' ? icon('check') : ''}</span><span class="t">${esc(x.title)}</span><span class="when">${esc(when)}</span>
          <span class="d">${esc(x.detail.length > 90 ? `${x.detail.slice(0, 88)}…` : x.detail)}</span></li>`;
      }).join('')}</ol>
      ${eta ? `<div class="eta">${icon('clock')}<span>Estimated resolution time<b>${eta}</b></span></div>` : `<div class="eta">${icon('clock')}<span>Predicted after approval<b>${fmt(p.prediction.exposurePerHr.before)} → ${fmt(p.prediction.exposurePerHr.after)} veh/h into water</b></span></div>`}`;
  }

  function drawImpact() {
    const inc = selected();
    const el = $('impact-card');
    const head = `<div class="card-h"><h3>${icon('layers')}Impact before &amp; after</h3></div>`;
    if (!inc || !inc.execution) {
      el.innerHTML = `${head}<div class="empty">${inc && inc.plan ? 'Approve the plan to compare conditions before and after the response.' : 'No response executed yet.'}</div>`;
      return;
    }
    const ex = inc.execution, s = inc.sector;
    const before = congestionPct(s, ex.before.links), after = congestionPct(s, state.traffic.links);
    el.innerHTML = `${head}
      <div class="mini-maps">
        <div class="mini-map"><svg class="map dark" id="mm-before"></svg><span class="tag before">Before response</span></div>
        <span class="mini-arrow">${icon('arrow')}</span>
        <div class="mini-map"><svg class="map dark" id="mm-now"></svg><span class="tag after">After response</span></div>
      </div>
      <div class="mini-stats">
        <div><span class="dot c-red"></span>Traffic: <b>${before}%</b> congested</div>
        <div><span class="dot c-green"></span>Traffic: <b>${after}%</b> congested</div>
        <div class="muted">${fmt(ex.before.metrics.exposurePerHr)} veh/h into water</div>
        <div class="muted"><b class="${state.traffic.metrics.exposurePerHr < ex.before.metrics.exposurePerHr ? 'delta-good' : ''}">${fmt(state.traffic.metrics.exposurePerHr)}</b> veh/h into water</div>
      </div>`;
    render($('mm-before'), world, { links: ex.before.links, sectorSev: { [s]: ex.before.severity }, incidents: [{ sector: s, severity: ex.before.severity }] }, { theme: 'dark', labels: false, selected: s });
    render($('mm-now'), world, { links: state.traffic.links, sectorSev: sectorSev(), depth: state.truth.depth, tickets: state.tickets, simMin: state.simMin }, { theme: 'dark', labels: false, selected: s, showDepth: true });
  }

  const FEED = { weather: ['rain', 'Weather update'], traffic: ['car', 'Traffic feed'], report: ['person', 'Citizen report'], alert: ['megaphone', 'Resident alert'], dispatch: ['truck', 'Dispatch'], incident: ['flood', 'Incident'], system: ['shield', 'System'], scenario: ['play', 'Scenario'] };
  function feedItems(list) {
    return list.map((e) => {
      const [ic, title] = FEED[e.type] || ['radio', e.type];
      return `<li><span class="fi ${e.type}">${icon(ic)}</span><b>${title}</b><span class="when">${e.clock}</span><span class="d">${esc(e.text.length > 110 ? `${e.text.slice(0, 108)}…` : e.text)}</span></li>`;
    }).join('');
  }
  function drawOvFeeds() {
    const list = state.events.slice().reverse().filter((e) => e.type !== 'system').slice(0, 5);
    $('ov-feeds').innerHTML = list.length ? feedItems(list) : '<li class="empty" style="display:block">No activity yet.</li>';
  }

  // ------------------------------------------------------------ incidents view
  function drawIncidentList() {
    const list = state.incidents.slice().sort((a, b) => (a.status === 'resolved') - (b.status === 'resolved') || RANK[b.severity] - RANK[a.severity] || b.openedAt - a.openedAt);
    $('inc-count').textContent = `${list.filter((i) => i.status !== 'resolved').length} active · ${list.length} total`;
    keepScroll($('inc-list'), list.length ? list.map((i) => `
      <button class="inc-item ${i.id === selIncident ? 'active' : ''}" data-id="${i.id}" data-sector="${i.sector}">
        ${sevBadge(i.status === 'resolved' ? 'Watch' : i.severity, i.status === 'resolved' ? 'Resolved' : i.severity)}
        <span><span class="id">${i.id}</span> · <b>${esc(place(i))}</b></span>
        <span class="status ${i.status === 'resolved' ? 'done' : waiting(i) ? 'proposed' : ''}">${i.status === 'resolved' ? 'resolved' : waiting(i) ? 'awaiting approval' : i.severity === 'Watch' && !i.execution ? 'monitoring' : i.status}</span>
        <span class="meta">opened ${i.openedClock} · peak ${i.peakSeverity}${i.assessment ? ` · ${i.assessment.score}/100 · ${i.assessment.confidence} confidence` : ''}</span>
      </button>`).join('') : '<div class="empty">No incidents.</div>');
    $('inc-list').querySelectorAll('.inc-item').forEach((b) => b.addEventListener('click', () => { userPicked = true; selIncident = b.dataset.id; selSector = b.dataset.sector; draw(); }));
  }

  function drawDetail() {
    const a = state.sectors[selSector];
    const inc = selected();
    const c = a.signals.citizen, w = a.signals.weather, t = a.signals.traffic, bd = a.breakdown;
    const total = Math.max(100, bd.citizen + bd.weather + bd.traffic + bd.corroboration + bd.hazard);
    const reports = state.reports.filter((r) => r.sector === selSector).slice(-6).reverse();
    let html = `<div class="card-h"><h3>${icon('flood')}${inc ? `${inc.id} · ` : ''}${esc(inc ? place(inc) : `Sector ${selSector}`)}</h3>
        <div class="right">${state.ai.enabled && inc ? `<button class="btn sm" id="reassess">${icon('brain')}Ask Gemini</button>` : ''}</div></div>
      <div class="gauge"><div class="score" style="color:${SEV_FILL[a.severity]}">${a.score}</div>
        <div>${sevBadge(a.severity)}<div class="conf" style="margin-top:6px">${a.confidence} confidence · ${a.confidencePct}% · ${esc(a.engine)}</div></div></div>
      <div class="stack"><span class="c" style="width:${(bd.citizen / total) * 100}%"></span><span class="w" style="width:${(bd.weather / total) * 100}%"></span><span class="t" style="width:${(bd.traffic / total) * 100}%"></span><span class="b" style="width:${(bd.corroboration / total) * 100}%"></span><span class="h" style="width:${(bd.hazard / total) * 100}%"></span></div>
      <div class="stack-legend"><span><i style="background:#7C3AED"></i>citizens ${bd.citizen}</span><span><i style="background:#2563EB"></i>weather ${bd.weather}</span><span><i style="background:#EA580C"></i>traffic ${bd.traffic}</span><span><i style="background:#64748B"></i>corroboration +${bd.corroboration}</span><span><i style="background:#DC2626"></i>hazard +${bd.hazard}</span></div>
      <div class="explain">${esc(a.explanation)}</div>
      <div class="signals">
        <div class="signal"><div class="k">Citizens</div><div class="v">${c.uniqueReporters} <span class="small muted">reporters</span></div><div class="small muted">${c.hazards.map((h) => h.replace(/_/g, ' ')).join(', ') || 'no hazards'}</div><div class="bar"><span style="width:${pct(c.score)};background:#7C3AED"></span></div></div>
        <div class="signal"><div class="k">Weather</div><div class="v">${w.rainMmHr} <span class="small muted">mm/h</span></div><div class="small muted">${w.alert === 'none' ? 'no alert' : `${w.alert} alert`}</div><div class="bar"><span style="width:${pct(w.score)}"></span></div></div>
        <div class="signal"><div class="k">Traffic</div><div class="v">${t.spike}× <span class="small muted">${t.trend}</span></div><div class="small muted">${t.note ? esc(t.note) : `travel time ${t.tti}× vs ${t.baseline}`}</div><div class="bar"><span style="width:${pct(t.score)};background:#EA580C"></span></div></div>
      </div>`;
    if (reports.length) {
      html += `<h4 style="margin:16px 0 6px">Citizen reports</h4><div class="feed" style="max-height:180px">${reports.map((r) => `<div><span class="t">${r.clock}</span><b>${esc(r.name)}</b>: “${esc(r.text)}” ${r.hasPhoto ? `<a href="/api/reports/${r.id}/photo" target="_blank" rel="noopener">${icon('photo')}</a>` : ''}<br><span class="muted">${esc(r.ai.summary)}</span></div>`).join('')}</div>`;
    }
    if (inc && inc.plan) {
      const p = inc.plan;
      const icons = { route: 'route', truck: 'truck', medic: 'medic', bell: 'megaphone' };
      html += `<h4 style="margin:18px 0 6px">Response plan v${p.version} · ${p.priority}</h4>
        <div class="actions">${p.actions.map((x) => `<div class="action"><span class="ico">${icon(icons[x.icon] || 'bolt')}</span><span class="t">${esc(x.title)}</span><span class="status ${x.status}">${x.status === 'done' ? `done ${x.executedClock || ''}` : 'proposed'}</span>
          <span class="d">${esc(x.detail)}${x.message ? `<div class="alert-msg">${esc(x.message.en)}<br><span class="muted">${esc(x.message.ur)}</span></div>` : ''}</span></div>`).join('')}</div>
        <div class="prediction"><div>Avg trip<b>${p.prediction.avgTripMin.before} → ${p.prediction.avgTripMin.after} min</b></div><div>Into floodwater<b>${fmt(p.prediction.exposurePerHr.before)} → ${fmt(p.prediction.exposurePerHr.after)}/h</b></div><div>Est. depth<b>~${p.prediction.depthGuessCm} cm</b></div></div>
        ${waiting(inc) ? `<button class="btn ${RANK[p.severity] >= 3 ? 'danger' : 'primary'}" id="execute2" style="width:100%;margin-top:12px;padding:11px">${icon('check')}Approve &amp; execute ${inc.execution ? 'escalation' : 'plan'}</button>` : ''}`;
    } else if (!inc) html += `<div class="empty">No incident for ${selSector}.</div>`;
    $('detail').innerHTML = html;
    const ex = $('execute2');
    if (ex) ex.onclick = () => { ex.disabled = true; ex.textContent = 'Executing…'; api(`/api/incidents/${inc.id}/execute`).catch((e) => { alert(e.message); ex.disabled = false; }); };
    const ra = $('reassess');
    if (ra) ra.onclick = () => { ra.disabled = true; ra.textContent = 'Asking…'; api(`/api/incidents/${inc.id}/reassess`).catch(() => {}); };
  }

  function drawTrail() {
    const inc = selected();
    $('trail-sub').textContent = inc ? inc.id : '';
    keepScroll($('trail'), inc ? inc.trail.slice().reverse().map((t) => `
      <li class="k-${t.kind}"><div class="when">${t.clock} · ${t.kind}${t.snapshot ? ` · ${t.snapshot.score}/100 ${t.snapshot.severity}` : ''}</div><div class="small">${esc(t.text)}</div></li>`).join('')
      : '<div class="empty">Select an incident.</div>');
  }

  // ------------------------------------------------------------ other views
  function drawBigMap() {
    render($('big-map'), world, { links: state.traffic.links, sectorSev: sectorSev(), depth: state.truth.depth, reports: state.reports.filter((r) => state.simMin - r.t <= 30), tickets: state.tickets, simMin: state.simMin, incidents: mapIncidents() },
      { theme: 'dark', selected: selSector, showDepth: $('show-depth').checked, showReports: true, showDepots: true, onSelect: onMapSelect });
  }
  function drawSectorTable() {
    const rows = Object.keys(world.sectors).sort((a, b) => state.sectors[b].score - state.sectors[a].score);
    keepScroll($('sector-table'), `<tr><th>Sector</th><th>Rain</th><th>Alert</th><th>Traffic</th><th>Score</th><th>Severity</th></tr>` + rows.map((s) => {
      const w = state.weather[s], t = state.traffic.sectors[s], a = state.sectors[s];
      return `<tr data-s="${s}"><td><b>${s}</b></td><td>${w.rainMmHr} mm/h</td><td>${w.alert === 'none' ? '<span class="muted">none</span>' : `<span class="chip">${w.alert}</span>`}</td><td>${(t.tti / t.baseline).toFixed(1)}×</td><td>${a.score}</td><td>${sevBadge(a.severity)}</td></tr>`;
    }).join(''));
    $('sector-table').querySelectorAll('tr[data-s]').forEach((tr) => tr.addEventListener('click', () => { onMapSelect(tr.dataset.s); location.hash = '#incidents'; }));
  }
  function drawTickets() {
    const list = state.tickets.slice().reverse();
    keepScroll($('tickets-view'), list.length ? list.map((tk) => {
      const idx = STEPS.findIndex((s) => tk.status.startsWith(s));
      return `<div class="ticket"><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">${icon(tk.kind === 'rescue' ? 'medic' : 'truck')}<b class="mono">${tk.id}</b><span>${esc(tk.team)}</span><span class="pill">${tk.priority}</span><span class="muted small">${tk.incidentId} · ${tk.sector}</span><span class="status ${tk.status === 'Resolved' ? 'done' : ''}" style="margin-left:auto">${esc(tk.status)}</span></div>
        <div class="steps">${STEPS.map((s, i) => `<span class="${i <= idx ? 'on' : ''}" title="${s}"></span>`).join('')}</div>
        <div class="small muted" style="margin-top:6px">${esc(tk.history[tk.history.length - 1].note || '')} · ETA ${tk.etaMin} min</div></div>`;
    }).join('') : '<div class="empty">No dispatches yet.</div>');
  }
  function drawAlerts() {
    const list = state.broadcasts.slice().reverse();
    keepScroll($('alerts-view'), list.length ? `<ul class="feedlist">${list.map((b) => `<li><span class="fi ${b.kind === 'all_clear' ? 'dispatch' : 'alert'}">${icon(b.kind === 'all_clear' ? 'check' : 'megaphone')}</span>
      <b>${b.kind === 'all_clear' ? 'All clear' : 'Flood warning'} · ${b.sectors.join(', ')}</b><span class="when">${b.clock}</span>
      <span class="d">${esc(b.message.en)}<br>${esc(b.message.ur)}<br><b>${fmt(b.delivered)}</b> delivered · ${b.failed} failed · ${(b.channels || ['SMS']).join(', ')}</span></li>`).join('')}</ul>` : '<div class="empty">No alerts sent yet.</div>');
  }
  function drawWeatherSource(live) {
    const ws = state.weatherSource || {};
    const pmd = Object.entries(ws.pmdAlerts || {}).map(([s, a]) => `${s} ${a.level} (by ${esc(a.by)}, until ${a.until})`);
    $('wx-source').innerHTML = !live ? 'Demo: scripted storm and mock PMD alerts.'
      : `<span class="${ws.ok ? 'ok-mark' : 'bad-mark'}"></span>Rain from <b>${esc(ws.provider)}</b>${ws.fetchedAt ? `, updated ${ws.fetchedAt}` : ''}${ws.ok ? '' : ' (unavailable, retrying)'}<br>Automatic alert at ${(ws.thresholdsMmHr || []).join(' / ')} mm/h<br>PMD alerts: ${pmd.length ? pmd.join(', ') : 'none entered'}<br><span class="muted" style="font-size:11px">${esc(ws.attribution || '')}</span>`;
    $('w-title').textContent = live ? 'Enter PMD alert' : 'Set weather alert (demo)';
    $('w-help').textContent = live ? 'Record an official PMD alert for a sector. It lasts 6 hours. Rain stays live.' : 'Override the demo weather for a sector.';
    $('w-rain').classList.toggle('hidden', live);
    $('w-clear').textContent = live ? 'Clear PMD alert' : 'Clear override';
  }
  function drawLog() {
    const el = $('log');
    const atTop = el.scrollTop < 10;
    el.innerHTML = state.events.slice().reverse().map((e) => `<div><span class="t">${e.clock}</span><span class="chip">${e.type}</span>${esc(e.text)}</div>`).join('');
    if (atTop) el.scrollTop = 0;
  }
})();
