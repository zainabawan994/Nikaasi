(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ORDER = ['F-11', 'F-10', 'F-9', 'F-8', 'G-11', 'G-10', 'G-9', 'G-8', 'H-11', 'H-10', 'H-9', 'H-8'];
  const LABEL = { Watch: 'Normal', Moderate: 'Watch out', High: 'Flooding', Critical: 'Danger' };

  async function load() {
    try {
      const st = await fetch('/api/state').then((r) => r.json());  // public view: no incident details
      render(st);
    } catch {
      $('now-updated').textContent = 'offline, retrying…';
    }
  }

  function render(st) {
    const ws = st.weatherSource || {};
    $('now-updated').textContent = ws.mode === 'live' ? `updated ${ws.fetchedAt || st.clock}` : `demo · ${st.clock}`;
    $('sector-grid').innerHTML = ORDER.filter((s) => st.sectors[s]).map((s) => {
      const a = st.sectors[s], w = st.weather[s] || {};
      const rain = Number(w.rainMmHr || 0);
      return `<a class="sector-tile sev-bg-${a.severity}" href="citizen.html" title="${esc(s)}: ${LABEL[a.severity]}">
        <b>${esc(s)}</b><span>${rain > 0 ? `🌧 ${rain} mm/h` : '☀︎ dry'}</span><small>${LABEL[a.severity]}</small></a>`;
    }).join('');

    const warnings = (st.broadcasts || []).slice().reverse().slice(0, 4);
    $('warnings').innerHTML = warnings.length ? warnings.map((b) => `
      <div class="warning ${b.kind === 'all_clear' ? 'clear' : ''}">
        <div class="small muted">${esc(b.clock)} · ${esc(b.sectors.join(', '))}</div>
        <div>${esc(b.message.en)}</div>
        <div class="small muted">${esc(b.message.ur)}</div>
      </div>`).join('') : '<div class="empty">No warnings right now. Stay safe.</div>';

    $('attribution').textContent = ws.attribution || '';
  }

  load();
  setInterval(load, 60000);
})();
