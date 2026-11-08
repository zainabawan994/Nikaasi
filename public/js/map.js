/* Shared schematic map renderer (SVG). theme: 'dark' (staff operations map) | 'light' (citizen app). */
(function () {
  const SEV_FILL = { Watch: '#22C55E', Moderate: '#EAB308', High: '#F97316', Critical: '#EF4444' };
  const THEMES = {
    dark: {
      block: '#1A2537', blockStroke: '#26344A', label: '#7E93B2', roadLabel: '#5D708D', local: 0.55, calm: 0.42,
      sevAlpha: { Watch: 0, Moderate: 0.18, High: 0.26, Critical: 0.34 }, glow: true,
      tti: ['#22C55E', '#EAB308', '#F97316', '#EF4444', '#B91C1C'], closed: '#475569',
      marker: '#0D1F3C', markerText: '#E2E8F0', water: '#3B82F6',
    },
    light: {
      block: '#F8FAFC', blockStroke: '#DCE3EC', label: '#94A3B8', roadLabel: '#94A3B8', local: 0.8, calm: 0.75,
      sevAlpha: { Watch: 0, Moderate: 0.16, High: 0.22, Critical: 0.28 }, glow: false,
      tti: ['#22C55E', '#EAB308', '#F97316', '#EF4444', '#B91C1C'], closed: '#94A3B8',
      marker: '#0D1F3C', markerText: '#FFFFFF', water: '#3B82F6',
    },
  };

  function ttiColor(t, th = THEMES.dark) {
    if (t < 1.3) return th.tti[0];
    if (t < 2) return th.tti[1];
    if (t < 4) return th.tti[2];
    if (t < 10) return th.tti[3];
    return th.tti[4];
  }
  function hash(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return (h >>> 0) / 4294967295; }
  function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  let uid = 0;

  /**
   * data: { links, sectorSev, depth?, reports?, tickets?, simMin?, incidents? [{sector, title, severity}] }
   * opts: { theme, selected, onSelect, showDepth, showReports, showDepots, labels, pin }
   */
  function render(svg, world, data, opts = {}) {
    const theme = opts.theme || 'dark';
    const th = THEMES[theme];
    const { w, h } = world.size;
    const id = `m${++uid}`;
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    svg.classList.toggle('dark', theme === 'dark');
    const N = world.nodes;
    let out = `<defs><filter id="${id}g" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
      <radialGradient id="${id}r"><stop offset="0" stop-color="#EF4444" stop-opacity=".55"/><stop offset="1" stop-color="#EF4444" stop-opacity="0"/></radialGradient></defs>`;

    // city blocks
    Object.values(world.sectors).forEach((s) => {
      const sev = (data.sectorSev && data.sectorSev[s.id]) || 'Watch';
      const sel = opts.selected === s.id;
      out += `<rect class="sector" data-sector="${s.id}" x="${s.x + 4}" y="${s.y + 4}" width="${s.w - 8}" height="${s.h - 8}" rx="6" fill="${th.block}" stroke="${sel ? '#60A5FA' : th.blockStroke}" stroke-width="${sel ? 2 : 1}"><title>${s.id}: ${sev}</title></rect>`;
      if (th.sevAlpha[sev] > 0) out += `<rect x="${s.x + 4}" y="${s.y + 4}" width="${s.w - 8}" height="${s.h - 8}" rx="6" fill="${SEV_FILL[sev]}" fill-opacity="${th.sevAlpha[sev]}" pointer-events="none"/>`;
      if (opts.showDepth && data.depth) {
        const d = data.depth[s.id] || 0;
        if (d > 0.5) {
          const hh = Math.min(1, d / 60) * (s.h - 12);
          out += `<rect x="${s.x + 6}" y="${s.y + s.h - 6 - hh}" width="${s.w - 12}" height="${hh}" rx="5" fill="${th.water}" fill-opacity=".28" pointer-events="none"/>`;
          out += `<g pointer-events="none"><rect x="${s.cx - 42}" y="${s.cy + 24}" width="84" height="20" rx="10" fill="${th.water}"/><text x="${s.cx}" y="${s.cy + 38}" text-anchor="middle" font-size="11.5" font-weight="700" fill="#fff">Water ${Math.round(d)} cm</text></g>`;
        }
      }
      out += `<text class="sector-label" x="${s.cx}" y="${s.cy - 16}" text-anchor="middle" fill="${th.label}">${s.id}</text>`;
    });

    // roads (glowing on the dark map)
    const linkData = data.links || {};
    const sorted = [...world.links].sort((a, b) => (a.kind === 'local' ? -1 : 1) - (b.kind === 'local' ? -1 : 1));
    let roads = '', marks = '';
    sorted.forEach((l) => {
      const a = N[l.a], b = N[l.b];
      const d = linkData[l.id] || { tti: 1 };
      const width = l.kind === 'local' ? 3 : l.id.startsWith('h') && l.id.endsWith('_2') ? 9 : 6;
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      if (d.closed) {
        roads += `<line class="road" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="${th.closed}" stroke-width="${width}" stroke-dasharray="7 6"><title>${esc(l.name)}: closed</title></line>`;
        marks += `<g transform="translate(${mx},${my})" pointer-events="none"><circle r="11" fill="#EF4444" stroke="#fff" stroke-width="2"/><path d="M-5.5,0 H5.5" stroke="#fff" stroke-width="3" stroke-linecap="round"/></g>`;
      } else {
        const col = ttiColor(d.tti, th);
        const calm = d.tti < 1.3;  // free-flowing roads stay quiet so problems stand out
        const op = l.kind === 'local' ? th.local * (calm ? 0.6 : 1) : calm ? th.calm : 0.95;
        roads += `<line class="road" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="${col}" stroke-width="${width}" stroke-opacity="${op}" ${th.glow && d.tti >= 2 ? `filter="url(#${id}g)"` : ''}><title>${esc(l.name)}: ${d.tti}× normal travel time${d.flow != null ? `, ${d.flow} veh/h` : ''}</title></line>`;
      }
      if (l.underpass) marks += `<g transform="translate(${mx - 34},${my + 11})" pointer-events="none"><rect width="68" height="17" rx="8.5" fill="${th.marker}" stroke="#334155"/><text x="34" y="12.5" text-anchor="middle" font-size="10" font-weight="700" fill="${th.markerText}">Underpass</text></g>`;
    });
    out += roads;

    if (opts.labels !== false) {
      const seen = new Set();
      world.links.filter((l) => l.kind === 'arterial').forEach((l) => {
        const base = l.name.split(' — ')[0];
        if (seen.has(base)) return;
        const a = N[l.a], b = N[l.b];
        const attrs = `class="road-label" fill="${th.roadLabel}"`;
        if (a.y === b.y && l.id.startsWith('h3')) { seen.add(base); out += `<text ${attrs} x="${a.x + 8}" y="${a.y + 16}">${esc(base)}</text>`; }
        else if (a.y === b.y && l.id.startsWith('h0')) { seen.add(base); out += `<text ${attrs} x="${a.x + 8}" y="${a.y - 8}">${esc(base)}</text>`; }
        else if (a.y === b.y) { seen.add(base); out += `<text ${attrs} x="${a.x + 8}" y="${a.y - 9}">${esc(base)}</text>`; }
        else if (l.id.endsWith('_0')) { seen.add(base); out += `<text ${attrs} transform="translate(${a.x - 8},${a.y + 14}) rotate(90)">${esc(base)}</text>`; }
      });
    }
    out += marks;

    if (opts.showDepots) {
      world.depots.forEach((d) => {
        const n = N[d.node];
        const letter = { drain: 'D', rescue: 'R', police: 'P' }[d.kind];
        out += `<g transform="translate(${n.x},${n.y})"><rect x="-11" y="-11" width="22" height="22" rx="6" fill="${th.marker}" stroke="#475569"/><text y="4.5" text-anchor="middle" font-size="12" font-weight="800" fill="${th.markerText}">${letter}</text><title>${esc(d.name)}</title></g>`;
      });
    }

    if (opts.showReports && data.reports) {
      data.reports.forEach((r) => {
        const s = world.sectors[r.sector];
        if (!s) return;
        const x = s.x + 24 + hash(r.id) * (s.w - 48), y = s.y + 42 + hash(r.id + 'y') * (s.h - 64);
        out += `<g transform="translate(${x},${y})"><circle r="5" fill="#A78BFA" stroke="#fff" stroke-width="1.5"/><title>${esc(r.name)} ${r.clock}: ${esc(r.text)}</title></g>`;
      });
    }

    if (data.tickets && data.simMin != null) {
      data.tickets.filter((t) => t.status !== 'Resolved').forEach((t) => {
        const pos = ticketPosition(world, t, data.simMin);
        if (!pos) return;
        const letter = t.kind === 'rescue' ? 'R' : 'D';
        out += `<g transform="translate(${pos.x},${pos.y})"><circle r="13" fill="#2563EB" stroke="#fff" stroke-width="2.5"/><text y="4.5" text-anchor="middle" font-size="12" font-weight="800" fill="#fff">${letter}</text><title>${esc(t.team)}: ${esc(t.status)}</title></g>`;
      });
    }

    // incident pins with callout
    (data.incidents || []).forEach((inc) => {
      const s = world.sectors[inc.sector];
      if (!s) return;
      const col = SEV_FILL[inc.severity] || '#EF4444';
      const x = s.cx, y = s.cy + 6;
      out += `<g pointer-events="none"><circle cx="${x}" cy="${y}" r="46" fill="url(#${id}r)"/>
        <circle cx="${x}" cy="${y}" r="15" fill="${col}" stroke="#fff" stroke-width="3"/><text x="${x}" y="${y + 6}" text-anchor="middle" font-size="17" font-weight="900" fill="#fff">!</text>`;
      if (inc.title) {
        const label = esc(inc.title);
        const bw = Math.max(inc.title.length * 6.8 + 34, 128), bx = Math.min(Math.max(x - bw / 2, 4), w - bw - 4), by = y - 68;
        out += `<rect x="${bx}" y="${by}" width="${bw}" height="40" rx="8" fill="#fff"/><path d="M${x - 7},${by + 40} L${x},${by + 48} L${x + 7},${by + 40}z" fill="#fff"/>
          <circle cx="${bx + 13}" cy="${by + 14}" r="4" fill="${col}"/><text x="${bx + 23}" y="${by + 18}" font-size="12" font-weight="700" fill="#0F172A">${label}</text>
          <text x="${bx + 13}" y="${by + 33}" font-size="11" fill="#64748B">Severity: <tspan fill="${col}" font-weight="700">${esc(inc.severity)}</tspan></text>`;
      }
      out += '</g>';
    });

    if (opts.pin) {
      out += `<g transform="translate(${opts.pin.x},${opts.pin.y})" pointer-events="none"><path d="M0,0 C-11,-14 -11,-28 0,-28 C11,-28 11,-14 0,0Z" fill="#2563EB" stroke="#fff" stroke-width="2"/><circle cy="-18" r="4.5" fill="#fff"/></g>`;
    }

    svg.innerHTML = out;
    if (opts.onSelect) {
      svg.querySelectorAll('.sector').forEach((el) => el.addEventListener('click', (ev) => {
        const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
        const p = pt.matrixTransform(svg.getScreenCTM().inverse());
        opts.onSelect(el.dataset.sector, { x: Math.round(p.x), y: Math.round(p.y) });
      }));
    }
  }

  function ticketPosition(world, t, simMin) {
    const depot = world.depots.find((d) => d.team === t.team);
    if (!depot) return null;
    const N = world.nodes;
    const pts = [N[depot.node]];
    let cur = depot.node;
    (t.route || []).forEach((rid) => {
      const l = world.links.find((x) => x.id === rid);
      if (!l) return;
      cur = l.a === cur ? l.b : l.a;
      pts.push(N[cur]);
    });
    const age = simMin - t.createdAt;
    let f = 0;
    if (t.status === 'En route') f = Math.max(0, Math.min(0.95, (age - 2) / Math.max(1, t.etaMin)));
    else if (t.status.startsWith('On site')) f = 1;
    const segs = [];
    let total = 0;
    for (let i = 1; i < pts.length; i++) { const d = Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y); segs.push(d); total += d; }
    let rem = f * total;
    for (let i = 0; i < segs.length; i++) {
      if (rem <= segs[i]) { const k = segs[i] ? rem / segs[i] : 0; return { x: pts[i].x + (pts[i + 1].x - pts[i].x) * k, y: pts[i].y + (pts[i + 1].y - pts[i].y) * k }; }
      rem -= segs[i];
    }
    const last = pts[pts.length - 1];
    return { x: last.x + 18, y: last.y - 18 };
  }

  function legendHTML() {
    return `<div class="legend">
      <span><i style="background:#22C55E"></i>free flow</span><span><i style="background:#EAB308"></i>slow</span>
      <span><i style="background:#F97316"></i>congested</span><span><i style="background:#EF4444"></i>jammed</span>
      <span><i style="background:#B91C1C"></i>gridlock</span><span><i style="background:repeating-linear-gradient(90deg,#475569 0 5px,transparent 5px 9px)"></i>closed</span>
      <span><b>D</b> drain team · <b>R</b> rescue · <b>P</b> police</span>
    </div>`;
  }

  window.NikaasiMap = { render, legendHTML, ttiColor, esc, SEV_FILL };
})();
