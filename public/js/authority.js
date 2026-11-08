(function () {
  const $ = (id) => document.getElementById(id);
  const { esc, render, legendHTML } = window.NikaasiMap;
  const RANK = { Watch: 0, Moderate: 1, High: 2, Critical: 3 };
  let world = null, state = null, es = null, me = null;
  let selSector = 'G-10', selIncident = null, userPicked = false;

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
    $('user-chip').textContent = `${me.name} · ${me.role}`;
    if (me.role === 'admin') $('admin-link').classList.remove('hidden');
    $('app').classList.remove('hidden');
    boot();
  }).catch(() => { document.body.innerHTML = '<div class="empty">This server does not support sign-in. Run the FastAPI backend.</div>'; });

  $('logout').onclick = async () => {
    await fetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
    location.replace('/login.html');
  };

  // ------------------------------------------------------------ boot
  async function boot() {
    const c = await fetch('/api/config').then((r) => r.json());
    world = c.world;
    $('legend').innerHTML = legendHTML();
    Object.keys(world.sectors).sort().forEach((s) => $('w-sector').insertAdjacentHTML('beforeend', `<option>${s}</option>`));
    $('w-sector').value = 'G-10';
    es = new EventSource('/api/stream');  // session cookie is sent automatically (same origin)
    es.onmessage = (ev) => {
      state = JSON.parse(ev.data);
      if (!state.incidents) { es.close(); toLogin(); return; }  // session expired → public view only
      draw();
    };
    es.onerror = () => { $('run-badge').innerHTML = '<span class="dot"></span>reconnecting…'; };
  }

  $('play').onclick = () => { userPicked = false; selIncident = null; api('/api/scenario', { action: 'play' }); };
  $('pause').onclick = () => api('/api/scenario', { action: state && state.running ? 'pause' : 'resume' });
  $('step').onclick = () => api('/api/scenario', { action: 'step' });
  $('reset').onclick = () => { selIncident = null; userPicked = false; api('/api/scenario', { action: 'reset' }); };
  $('speed').onchange = (e) => api('/api/scenario', { action: 'speed', speed: Number(e.target.value) });
  $('auto').onchange = (e) => api('/api/settings', { autoExecute: e.target.checked });
  $('show-depth').onchange = () => draw();
  $('w-apply').onclick = () => api('/api/weather', { sector: $('w-sector').value, alert: $('w-level').value, rainMmHr: Number($('w-rain').value) }).catch((e) => alert(e.message));

  // ------------------------------------------------------------ helpers
  const waiting = (i) => i.planPending && i.status !== 'resolved' && RANK[i.severity] >= 1;
  const sevBadge = (s) => `<span class="sev sev-${s}">${s}</span>`;
  const pct = (x) => `${Math.round(x * 100)}%`;
  const incidentFor = (sector) => state.incidents.filter((i) => i.sector === sector).sort((a, b) => (a.status === 'resolved') - (b.status === 'resolved') || b.openedAt - a.openedAt)[0];
  function keepScroll(el, html) { const t = el.scrollTop; el.innerHTML = html; el.scrollTop = t; }
  function sparkline(arr, w = 120, h = 28) {
    if (!arr || arr.length < 2) return '';
    const max = Math.max(2, ...arr), min = 1;
    const pts = arr.map((v, i) => `${(i / (arr.length - 1)) * w},${h - ((v - min) / (max - min)) * (h - 2) - 1}`).join(' ');
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" fill="none" stroke="#EE8434" stroke-width="2"/></svg>`;
  }

  // ------------------------------------------------------------ draw
  function draw() {
    if (!state || !world) return;
    $('clock').textContent = state.clock;
    $('run-badge').innerHTML = `<span class="dot"></span>${state.running ? `live · ${state.speed}×` : 'paused'}${state.scenario ? ` · ${esc(state.scenario)}` : ''}`;
    $('pause').textContent = state.running ? '⏸ Pause' : '▶ Resume';
    $('ai-badge').textContent = state.ai.enabled ? 'AI: Gemini' : 'AI: offline';
    $('auto').checked = !!state.settings.autoExecute;
    $('speed').value = String(state.speed);

    // auto-follow the most severe active incident until the user clicks something
    const active = state.incidents.filter((i) => i.status !== 'resolved').sort((a, b) => RANK[b.severity] - RANK[a.severity] || a.openedAt - b.openedAt);
    if (!userPicked && active.length) { selSector = active[0].sector; selIncident = active[0].id; }
    if (selIncident && !state.incidents.find((i) => i.id === selIncident)) selIncident = null;
    if (!selIncident) { const i = incidentFor(selSector); selIncident = i ? i.id : null; }

    drawKpis(active);
    drawMap();
    drawIncidents();
    drawDetail();
    drawImpact();
    drawTrail();
    drawFeeds();
    drawLog();
  }

  function drawKpis(active) {
    const m = state.traffic.metrics, b = state.traffic.baselineMetrics;
    const top = active[0];
    const recent = state.reports.filter((r) => state.simMin - r.t <= 30).length;
    const alerted = state.broadcasts.filter((x) => x.kind === 'warning').reduce((a, x) => a + x.delivered, 0);
    const tripDelta = m.avgTripMin - b.avgTripMin;
    $('kpis').innerHTML = [
      ['Active incidents', active.length, `${state.incidents.length} total today`],
      ['Highest severity', top ? sevBadge(top.severity) : sevBadge('Watch'), top ? `${top.sector} · ${state.sectors[top.sector].score}/100` : 'all sectors calm'],
      ['Citizen reports (30 min)', recent, `${state.reports.length} total`],
      ['Residents alerted', alerted.toLocaleString('en-US'), `${state.broadcasts.length} broadcasts`],
      ['Avg trip time', `${m.avgTripMin} min`, `<span class="${tripDelta > 1 ? 'delta-bad' : 'delta-good'}">${tripDelta >= 0 ? '+' : ''}${tripDelta.toFixed(1)}</span> vs normal ${b.avgTripMin}`],
      ['Vehicles into floodwater', `${m.exposurePerHr.toLocaleString('en-US')}/h`, `${m.stalledVehicles} stalled · ${m.closedLinks} roads closed`],
    ].map(([l, v, d]) => `<div class="card kpi"><div class="label">${l}</div><div class="value">${v}</div><div class="delta muted">${d}</div></div>`).join('');
  }

  function sectorSev() { const o = {}; Object.entries(state.sectors).forEach(([k, v]) => { o[k] = v.severity; }); return o; }

  function drawMap() {
    render($('map'), world, {
      links: state.traffic.links, sectorSev: sectorSev(), depth: state.truth.depth,
      reports: state.reports.filter((r) => state.simMin - r.t <= 30), tickets: state.tickets, simMin: state.simMin,
    }, {
      selected: selSector, showDepth: $('show-depth').checked, showReports: true, showDepots: true,
      onSelect: (s) => { userPicked = true; selSector = s; const i = incidentFor(s); selIncident = i ? i.id : null; draw(); },
    });
  }

  function drawIncidents() {
    const list = state.incidents.slice().sort((a, b) => (a.status === 'resolved') - (b.status === 'resolved') || RANK[b.severity] - RANK[a.severity] || b.openedAt - a.openedAt);
    $('inc-count').textContent = `${list.filter((i) => i.status !== 'resolved').length} active`;
    keepScroll($('incidents'), list.length ? list.map((i) => `
      <button class="inc-item ${i.id === selIncident ? 'active' : ''}" data-id="${i.id}" data-sector="${i.sector}">
        ${sevBadge(i.status === 'resolved' ? 'Watch' : i.severity)}
        <span><span class="id">${i.id}</span> · <b>${i.sector}</b></span>
        <span class="status ${i.status === 'resolved' ? 'done' : waiting(i) ? 'proposed' : ''}">${i.status === 'resolved' ? 'resolved' : waiting(i) ? 'plan awaiting approval' : i.severity === 'Watch' && !i.execution ? 'monitoring' : i.status}</span>
        <span class="meta">opened ${i.openedClock} · peak ${i.peakSeverity}${i.assessment ? ` · ${i.assessment.score}/100 · ${i.assessment.confidence} confidence` : ''}</span>
      </button>`).join('') : '<div class="empty">No incidents.</div>');
    $('incidents').querySelectorAll('.inc-item').forEach((b) => b.addEventListener('click', () => { userPicked = true; selIncident = b.dataset.id; selSector = b.dataset.sector; draw(); }));
  }

  function drawDetail() {
    const a = state.sectors[selSector];
    const inc = state.incidents.find((i) => i.id === selIncident);
    const c = a.signals.citizen, w = a.signals.weather, t = a.signals.traffic;
    const bd = a.breakdown;
    const total = Math.max(100, bd.citizen + bd.weather + bd.traffic + bd.corroboration + bd.hazard);
    const reports = state.reports.filter((r) => r.sector === selSector).slice(-5).reverse();
    const tickets = inc ? state.tickets.filter((x) => x.incidentId === inc.id) : [];
    const STEPS = ['Created', 'Assigned', 'En route', 'On site', 'Resolved'];

    let html = `
      <h3>${inc ? `${inc.id} · ` : ''}Sector ${selSector} ${inc && inc.status === 'resolved' ? '<span class="status done">resolved</span>' : ''}
        <span class="right">${state.ai.enabled && inc ? `<button class="btn" id="reassess" style="padding:3px 8px;font-size:12px">↻ Ask Gemini</button>` : ''}</span></h3>
      <div class="gauge">
        <div class="score" style="color:${window.NikaasiMap.SEV_FILL[a.severity]}">${a.score}</div>
        <div>${sevBadge(a.severity)}<div class="conf" style="margin-top:4px">${a.confidence} confidence · ${a.confidencePct}%</div>
</div>
      </div>
      <div class="stack" title="Score breakdown">
        <span class="c" style="width:${(bd.citizen / total) * 100}%"></span><span class="w" style="width:${(bd.weather / total) * 100}%"></span>
        <span class="t" style="width:${(bd.traffic / total) * 100}%"></span><span class="b" style="width:${(bd.corroboration / total) * 100}%"></span><span class="h" style="width:${(bd.hazard / total) * 100}%"></span>
      </div>
      <div class="stack-legend"><span><i style="background:#3E7CB1"></i>citizens ${bd.citizen}</span><span><i style="background:#13A89E"></i>weather ${bd.weather}</span><span><i style="background:#EE8434"></i>traffic ${bd.traffic}</span><span><i style="background:#6C4AB6"></i>corroboration +${bd.corroboration}</span><span><i style="background:#D7263D"></i>hazard +${bd.hazard}</span></div>
      <div class="explain">${esc(a.explanation)}</div>
      <div class="signals">
        <div class="signal"><div class="k">Citizens</div><div class="v">${c.uniqueReporters} <span class="small muted">reporters</span></div><div class="small muted">${c.withPhoto} photo · ${c.hazards.map((h) => h.replace(/_/g, ' ')).join(', ') || 'no hazards'}</div><div class="bar"><span style="width:${pct(c.score)};background:#3E7CB1"></span></div></div>
        <div class="signal"><div class="k">Weather</div><div class="v">${w.rainMmHr} <span class="small muted">mm/h</span></div><div class="small muted">${w.alert === 'none' ? 'no alert' : `${w.alert.toUpperCase()} alert`}</div><div class="bar"><span style="width:${pct(w.score)}"></span></div></div>
        <div class="signal"><div class="k">Traffic</div><div class="v">${t.spike}× <span class="small muted">${t.trend}</span></div>${sparkline(state.trafficHistory[selSector])}<div class="small muted">${t.note ? esc(t.note) : `TTI ${t.tti} vs ${t.baseline}`}</div><div class="bar"><span style="width:${pct(t.score)};background:#EE8434"></span></div></div>
      </div>`;

    if (reports.length) {
      html += `<details style="margin-top:10px" open><summary class="small" style="cursor:pointer;font-weight:600">Citizen reports (${state.reports.filter((r) => r.sector === selSector).length})</summary>
        <div class="feed" style="max-height:150px">${reports.map((r) => `<div><span class="t">${r.clock}</span><b>${esc(r.name)}</b>: “${esc(r.text)}” ${r.hasPhoto ? `<a href="/api/reports/${r.id}/photo" target="_blank" rel="noopener">📷</a>` : ''}<br><span class="muted">${esc(r.ai.summary)}</span></div>`).join('')}</div></details>`;
    }

    if (inc && inc.plan) {
      const p = inc.plan;
      const icons = { route: '🚦', truck: '🚛', medic: '🚑', bell: '📲' };
      html += `<h3 style="margin-top:14px">🧭 Response plan v${p.version} <span class="sev sev-${p.severity}" style="font-size:10px">${p.severity}</span> <span class="right small muted">${p.priority}</span></h3>
        <div class="actions">${p.actions.map((x) => `
          <div class="action"><div class="ico">${icons[x.icon] || '•'}</div><div class="t">${esc(x.title)}</div><span class="status ${x.status}">${x.status === 'done' ? `done ${x.executedClock || ''}` : 'proposed'}</span>
          <div class="d">${esc(x.detail)}${x.message ? `<div class="alert-msg">${esc(x.message.en)}<br><span class="muted">${esc(x.message.ur)}</span></div>` : ''}</div></div>`).join('')}
        </div>
        <div class="prediction">
          <div>Avg trip<b>${p.prediction.avgTripMin.before} → ${p.prediction.avgTripMin.after} min</b></div>
          <div>Into floodwater<b>${p.prediction.exposurePerHr.before.toLocaleString('en-US')} → ${p.prediction.exposurePerHr.after.toLocaleString('en-US')}/h</b></div>
          <div>Est. depth<b>~${p.prediction.depthGuessCm} cm</b></div>
        </div>
        ${waiting(inc) ? `<button class="btn ${RANK[p.severity] >= 3 ? 'danger' : 'primary'}" id="execute" style="width:100%;justify-content:center;margin-top:10px;padding:10px">▶ Approve &amp; execute ${inc.execution ? 'escalation' : 'plan'}</button>` : ''}`;
    } else if (!inc) {
      html += `<div class="empty">No incident for ${selSector}.</div>`;
    }

    if (tickets.length) {
      html += `<h3 style="margin-top:14px">🎫 Dispatch tickets</h3><div class="tickets">${tickets.map((tk) => {
        const idx = STEPS.findIndex((s) => tk.status.startsWith(s));
        return `<div class="ticket"><b class="mono">${tk.id}</b> · ${esc(tk.team)} · ${tk.priority} <span class="status ${tk.status === 'Resolved' ? 'done' : ''}" style="float:right">${esc(tk.status)}</span>
          <div class="steps">${STEPS.map((s, i) => `<span class="${i <= idx ? 'on' : ''}" title="${s}"></span>`).join('')}</div>
          <div class="small muted" style="margin-top:4px">${esc(tk.history[tk.history.length - 1].note || '')} · ETA ${tk.etaMin} min</div></div>`;
      }).join('')}</div>`;
    }

    const bcs = inc ? state.broadcasts.filter((b) => b.incidentId === inc.id) : [];
    if (bcs.length) {
      html += `<h3 style="margin-top:14px">📲 Resident alerts sent</h3>${bcs.map((b) => `<div class="small" style="margin-bottom:4px"><b>${b.id}</b> ${b.clock} → ${b.sectors.join(', ')}: ${b.delivered.toLocaleString('en-US')} delivered, ${b.failed} failed</div>`).join('')}`;
    }

    $('detail').innerHTML = html;
    const ex = $('execute');
    if (ex) ex.onclick = () => { ex.disabled = true; ex.textContent = 'Executing…'; api(`/api/incidents/${inc.id}/execute`).catch((e) => { alert(e.message); ex.disabled = false; }); };
    const ra = $('reassess');
    if (ra) ra.onclick = () => { ra.disabled = true; ra.textContent = 'Asking…'; api(`/api/incidents/${inc.id}/reassess`).catch(() => {}); };
  }

  function drawImpact() {
    const inc = state.incidents.find((i) => i.id === selIncident);
    const el = $('impact');
    if (!inc || !inc.execution) {
      $('impact-sub').textContent = '';
      el.innerHTML = `<div class="empty">${inc && inc.plan && inc.status !== 'resolved' ? 'Execute the plan to see its impact.' : 'No response executed yet.'}</div>`;
      return;
    }
    const ex = inc.execution;
    const s = inc.sector;
    $('impact-sub').textContent = `${inc.id} · executed ${ex.executedClock} by ${ex.by}`;
    const now = state.traffic, sh = state.shadow;
    el.innerHTML = `
      <div class="grid mini-maps">
        <div><h4>⏱ At decision (${ex.executedClock})</h4><svg class="map" id="mm-before"></svg></div>
        <div><h4>✅ Now: with Nikaasi response</h4><svg class="map" id="mm-now"></svg></div>
        <div><h4>⚠ Now: if nobody had acted</h4><svg class="map" id="mm-shadow"></svg></div>
      </div>
      <table class="tbl" style="margin-top:10px">
        <tr><th>Metric</th><th>At decision</th><th>Now (with response)</th><th>No response</th></tr>
        ${row(`${s} travel-time index`, ex.before.sectorTTI, now.sectors[s].tti, sh ? sh.sectors[s].tti : null, '×')}
        ${row(`Zone travel-time index (${s} + neighbours)`, zone(s, ex.before.sectors), zone(s, mapT(now.sectors)), sh ? zone(s, mapT(sh.sectors)) : null, '×')}
        ${row('Avg trip time (network)', ex.before.metrics.avgTripMin, now.metrics.avgTripMin, sh ? sh.metrics.avgTripMin : null, ' min')}
        ${row('Vehicles entering floodwater', ex.before.metrics.exposurePerHr, now.metrics.exposurePerHr, sh ? sh.metrics.exposurePerHr : null, '/h')}
        ${row('Stalled vehicles', ex.before.metrics.stalledVehicles, now.metrics.stalledVehicles, sh ? sh.metrics.stalledVehicles : null, '')}
        ${row(`Water depth in ${s} (sim)`, null, state.truth.depth[s], state.truth.shadowDepth[s], ' cm')}
        ${row('Fused severity score', ex.before.score, inc.assessment.score, null, '')}
        <tr><td>Dispatch tickets</td><td>—</td><td colspan="2">${state.tickets.filter((t) => t.incidentId === inc.id).map((t) => `${t.id}: <b>${esc(t.status)}</b>`).join(' · ')}</td></tr>
      </table>`;
    const sevNow = sectorSev();
    render($('mm-before'), world, { links: ex.before.links, sectorSev: { [s]: ex.before.severity } }, { labels: false, selected: s });
    render($('mm-now'), world, { links: now.links, sectorSev: sevNow, depth: state.truth.depth, tickets: state.tickets, simMin: state.simMin }, { labels: false, selected: s, showDepth: true });
    if (sh) render($('mm-shadow'), world, { links: sh.links, sectorSev: { [s]: 'Critical' }, depth: state.truth.shadowDepth }, { labels: false, selected: s, showDepth: true });
  }

  function mapT(sectors) { const o = {}; Object.entries(sectors).forEach(([k, v]) => { o[k] = v.tti; }); return o; }
  function zone(s, tti) {
    if (!tti) return null;
    const c = world.sectors[s];
    const ids = Object.values(world.sectors).filter((q) => Math.abs(q.i - c.i) + Math.abs(q.j - c.j) <= 1).map((q) => q.id);
    return +(ids.reduce((a, id) => a + tti[id], 0) / ids.length).toFixed(2);
  }

  function row(label, before, now, shadow, unit) {
    const f = (v) => (v == null ? '—' : `${typeof v === 'number' ? v.toLocaleString('en-US') : v}${unit}`);
    const cls = before != null && now != null ? (now < before ? 'delta-good' : now > before ? 'delta-bad' : '') : (shadow != null && now != null && now < shadow ? 'delta-good' : '');
    return `<tr><td>${label}</td><td>${f(before)}</td><td class="${cls}">${f(now)}</td><td>${f(shadow)}</td></tr>`;
  }

  function drawTrail() {
    const inc = state.incidents.find((i) => i.id === selIncident);
    $('trail-sub').textContent = inc ? inc.id : '';
    keepScroll($('trail'), inc ? inc.trail.slice().reverse().map((t) => `
      <li class="k-${t.kind}"><div class="when">${t.clock} · ${t.kind.toUpperCase()}${t.snapshot ? ` · ${t.snapshot.score}/100 ${t.snapshot.severity} · ${t.snapshot.engine}` : ''}</div><div class="small">${esc(t.text)}</div></li>`).join('')
      : '<div class="empty">Select an incident.</div>');
  }

  function drawFeeds() {
    const rows = Object.keys(world.sectors).sort((a, b) => state.sectors[b].score - state.sectors[a].score);
    keepScroll($('feeds'), `<tr><th>Sector</th><th>Rain</th><th>Alert</th><th>Traffic</th><th>Score</th></tr>` + rows.map((s) => {
      const w = state.weather[s], t = state.traffic.sectors[s], a = state.sectors[s];
      return `<tr style="cursor:pointer" data-s="${s}"><td><b>${s}</b></td><td>${w.rainMmHr} mm/h</td><td>${w.alert === 'none' ? '—' : `<span class="chip">${w.alert}</span>`}</td>
        <td>${(t.tti / t.baseline).toFixed(1)}×</td><td>${a.score} ${sevBadge(a.severity).replace('class="sev', 'style="font-size:9px;padding:1px 6px" class="sev')}</td></tr>`;
    }).join(''));
    $('feeds').querySelectorAll('tr[data-s]').forEach((tr) => tr.addEventListener('click', () => { userPicked = true; selSector = tr.dataset.s; const i = incidentFor(selSector); selIncident = i ? i.id : null; draw(); }));
  }

  function drawLog() {
    const el = $('log');
    const atTop = el.scrollTop < 10;
    el.innerHTML = state.events.slice().reverse().map((e) => `<div><span class="t">${e.clock}</span><span class="chip">${e.type}</span>${esc(e.text)}</div>`).join('');
    if (atTop) el.scrollTop = 0;
  }
})();
