(function () {
  const $ = (id) => document.getElementById(id);
  const { esc } = window.NikaasiMap;
  let world = null, state = null, pin = null, gps = null, photo = null;

  const store = {
    get(k, d) { try { return localStorage.getItem(k) || d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };
  let deviceId = store.get('nikaasi.device', '');
  if (!deviceId) { deviceId = 'dev-' + Math.random().toString(36).slice(2, 10); store.set('nikaasi.device', deviceId); }
  let sector = store.get('nikaasi.sector', 'G-10');
  $('name').value = store.get('nikaasi.name', '');

  fetch('/api/config').then((r) => r.json()).then((c) => {
    world = c.world;
    const sel = $('sector');
    Object.keys(world.sectors).sort().forEach((s) => { sel.insertAdjacentHTML('beforeend', `<option value="${s}">${s}</option>`); });
    sel.value = sector;
    sel.addEventListener('change', () => { setSector(sel.value); pin = null; gps = null; draw(); });
    connect();
  });

  function setSector(s) { sector = s; store.set('nikaasi.sector', s); $('sector').value = s; renderStatus(); renderInbox(); }

  function connect() {
    const es = new EventSource('/api/stream');
    es.onmessage = (ev) => { state = JSON.parse(ev.data); $('clock').textContent = state.clock; renderStatus(); renderInbox(); draw(); };
  }

  function draw() {
    if (!world || !state) return;
    const sectorSev = {};
    Object.entries(state.sectors).forEach(([k, v]) => { sectorSev[k] = v.severity; });
    window.NikaasiMap.render($('map'), world, { links: state.traffic.links, sectorSev }, {
      selected: sector, labels: false, pin, theme: 'light',
      onSelect: (s, p) => { pin = p; gps = null; setSector(s); draw(); },
    });
  }

  function renderStatus() {
    if (!state) return;
    const a = state.sectors[sector];
    $('st-sector').textContent = sector;
    $('st-sev').className = `sev sev-${a.severity}`;
    $('st-sev').textContent = { Watch: 'Normal', Moderate: 'Watch out', High: 'Flooding', Critical: 'Danger' }[a.severity];
    $('status').className = `status-card s-${a.severity}`;
    const w = state.weather[sector];
    const closures = state.closed.map((id) => world.links.find((l) => l.id === id)).filter((l) => l && l.sectors.includes(sector)).map((l) => l.name);
    const msgs = {
      Watch: 'No flooding confirmed in your sector.',
      Moderate: 'Possible flooding. Authorities are monitoring.',
      High: 'Flooding confirmed. Avoid low roads and underpasses.',
      Critical: 'Severe flooding. Stay indoors if you can. Emergency: 1122.',
    };
    $('st-text').innerHTML = `${msgs[a.severity]}<br>Rain ${w.rainMmHr} mm/h${w.alert !== 'none' ? ` · <b>${w.alert.toUpperCase()} alert</b>` : ''}${closures.length ? `<br>Closed roads: ${esc([...new Set(closures)].join(', '))}` : ''}`;
  }

  function renderInbox() {
    if (!state) return;
    const list = state.broadcasts.filter((b) => b.sectors.includes(sector)).slice().reverse();
    $('inbox').innerHTML = list.length ? list.map((b) => `
      <div class="msg ${b.kind}">
        <div class="small muted">${b.clock} · ${b.kind === 'all_clear' ? 'All clear' : 'Flood warning'}</div>
        <div style="margin-top:4px">${esc(b.message.en)}</div>
        <div class="small muted" style="margin-top:4px">${esc(b.message.ur)}</div>
      </div>`).join('') : '<div class="empty">No alerts right now. Stay safe.</div>';
  }

  $('chips').addEventListener('click', (e) => {
    if (e.target.tagName !== 'BUTTON') return;
    const d = $('desc');
    d.value = (d.value ? d.value.replace(/\s*$/, '. ') : '') + e.target.textContent;
    d.focus();
  });

  $('gps').addEventListener('click', () => {
    if (!navigator.geolocation) { $('err').textContent = 'GPS not available — pick your sector instead.'; return; }
    $('gps').textContent = 'Locating…';
    navigator.geolocation.getCurrentPosition((p) => {
      $('gps').textContent = 'Use GPS';
      gps = { lat: p.coords.latitude, lng: p.coords.longitude };
      // snap client-side for the UI; server snaps again
      let best = null, bd = Infinity;
      Object.values(world.sectors).forEach((s) => { const d = Math.hypot(s.latlng[0] - gps.lat, s.latlng[1] - gps.lng); if (d < bd) { bd = d; best = s.id; } });
      if (bd < 0.03) { setSector(best); pin = null; draw(); $('err').textContent = ''; } else { $('err').textContent = 'You seem to be outside the covered sectors — please pick one.'; gps = null; }
    }, () => { $('gps').textContent = 'Use GPS'; $('err').textContent = 'Could not get location — pick your sector instead.'; }, { timeout: 8000 });
  });

  $('photo').addEventListener('change', async (e) => {
    const f = e.target.files[0];
    if (!f) { photo = null; $('photo-prev').classList.add('hidden'); return; }
    try {
      photo = await compress(f);
      $('photo-prev').src = photo; $('photo-prev').classList.remove('hidden');
    } catch { $('err').textContent = 'Could not read that image.'; photo = null; }
  });

  function compress(file) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const k = Math.min(1, 1024 / Math.max(img.width, img.height));
        const c = document.createElement('canvas');
        c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(img.src);
        resolve(c.toDataURL('image/jpeg', 0.72));
      };
      img.onerror = reject;
      img.src = URL.createObjectURL(file);
    });
  }

  $('submit').addEventListener('click', async () => {
    $('err').textContent = '';
    const text = $('desc').value.trim();
    if (text.length < 3) { $('err').textContent = 'Please describe what you see.'; return; }
    store.set('nikaasi.name', $('name').value.trim());
    const btn = $('submit');
    btn.disabled = true; btn.textContent = 'Analysing…';
    try {
      const body = { sector, text, name: $('name').value.trim() || 'Resident', reporterId: deviceId, photo };
      if (gps) Object.assign(body, gps);
      else if (pin) Object.assign(body, pin);
      const r = await fetch('/api/reports', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || 'Failed');
      showResult(j);
      $('desc').value = ''; $('photo').value = ''; photo = null; $('photo-prev').classList.add('hidden');
    } catch (e) {
      $('err').textContent = e.message;
    } finally {
      btn.disabled = false; btn.textContent = 'Send report';
    }
  });

  function showResult({ report, sectorStatus, incidentId }) {
    const s = sectorStatus;
    const el = $('result');
    el.classList.remove('hidden');
    el.innerHTML = `
      <h3>Report ${report.id} received <span class="right small muted">${report.clock}</span></h3>
      <div style="margin:4px 0 10px"><b>${esc(report.ai.summary)}</b></div>
      <div class="explain small">
        ${incidentId
          ? `Authorities notified. ${s.sector} is <b>${s.severity}</b>.`
          : `Thanks. We'll act if more reports or data confirm it.`}
      </div>`;
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
})();
