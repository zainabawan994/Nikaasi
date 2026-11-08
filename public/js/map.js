/* Shared schematic map renderer (SVG) used by citizen + authority screens. */
(function () {
  const SEV_FILL = { Watch: '#8FA3B8', Moderate: '#E9B949', High: '#EE8434', Critical: '#D7263D' };
  const SEV_ALPHA = { Watch: 0.06, Moderate: 0.22, High: 0.3, Critical: 0.38 };

  function ttiColor(t) {
    if (t < 1.3) return '#2BB673';
    if (t < 2) return '#E9B949';
    if (t < 4) return '#EE8434';
    if (t < 10) return '#D7263D';
    return '#7A0F1D';
  }

  function hash(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return (h >>> 0) / 4294967295; }

  function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  /**
   * @param {SVGElement} svg
   * @param world  /api/config world
   * @param data   { links, sectorSev, depth?, reports?, tickets?, simMin? }
   * @param opts   { selected, onSelect, showDepth, showReports, showDepots, labels, pin }
   */
  function render(svg, world, data, opts = {}) {
    const { w, h } = world.size;
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    const N = world.nodes;
    let out = '';

    // sectors
    Object.values(world.sectors).forEach((s) => {
      const sev = (data.sectorSev && data.sectorSev[s.id]) || 'Watch';
      const sel = opts.selected === s.id;
      out += `<rect class="sector${sel ? ' selected' : ''}" data-sector="${s.id}" x="${s.x + 3}" y="${s.y + 3}" width="${s.w - 6}" height="${s.h - 6}" rx="10"
        fill="${SEV_FILL[sev]}" fill-opacity="${SEV_ALPHA[sev]}" ${sel ? '' : 'stroke="#C9D4DE" stroke-width="1"'}><title>${s.id} — ${sev}</title></rect>`;
      if (opts.showDepth && data.depth) {
        const d = data.depth[s.id] || 0;
        if (d > 0.5) {
          const hh = Math.min(1, d / 60) * (s.h - 10);
          out += `<rect x="${s.x + 5}" y="${s.y + s.h - 5 - hh}" width="${s.w - 10}" height="${hh}" rx="8" fill="#2E86DE" fill-opacity=".28" pointer-events="none"/>`;
          out += `<g pointer-events="none"><rect x="${s.cx - 34}" y="${s.cy + 22}" width="68" height="20" rx="10" fill="#fff" fill-opacity=".9"/><text x="${s.cx}" y="${s.cy + 36}" text-anchor="middle" font-size="12" font-weight="700" fill="#1B5FA8">💧 ${Math.round(d)} cm</text></g>`;
        }
      }
      out += `<text class="sector-label" x="${s.cx}" y="${s.cy - 18}" text-anchor="middle">${s.id}</text>`;
    });

    // roads
    const linkData = data.links || {};
    const sorted = [...world.links].sort((a, b) => (a.kind === 'local' ? -1 : 1) - (b.kind === 'local' ? -1 : 1));
    sorted.forEach((l) => {
      const a = N[l.a], b = N[l.b];
      const d = linkData[l.id] || { tti: 1 };
      const width = l.kind === 'local' ? 3.5 : l.id.startsWith('h') && l.id.endsWith('_2') ? 10 : 7;
      if (d.closed) {
        out += `<line class="road" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="#5B6B7B" stroke-width="${width}" stroke-dasharray="8 7" opacity=".85"><title>${esc(l.name)} — CLOSED</title></line>`;
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        out += `<g transform="translate(${mx},${my})" pointer-events="none"><circle r="11" fill="#fff" stroke="#D7263D" stroke-width="2.5"/><path d="M-5,-5 L5,5 M5,-5 L-5,5" stroke="#D7263D" stroke-width="3" stroke-linecap="round"/></g>`;
      } else {
        out += `<line class="road" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="${ttiColor(d.tti)}" stroke-width="${width}"><title>${esc(l.name)} — ${d.tti}× normal travel time${d.flow != null ? `, ${d.flow} veh/h` : ''}</title></line>`;
      }
      if (l.underpass) {
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        out += `<g transform="translate(${mx - 30},${my + 10})" pointer-events="none"><rect width="60" height="16" rx="4" fill="#0B2545"/><text x="30" y="12" text-anchor="middle" font-size="10" font-weight="700" fill="#fff">UNDERPASS</text></g>`;
      }
    });

    // road names (arterials only, once per road)
    if (opts.labels !== false) {
      const seen = new Set();
      world.links.filter((l) => l.kind === 'arterial').forEach((l) => {
        const base = l.name.split(' — ')[0];
        if (seen.has(base)) return;
        const a = N[l.a], b = N[l.b];
        if (a.y === b.y && l.id.startsWith('h3')) { seen.add(base); out += `<text class="road-label" x="${a.x + 8}" y="${a.y + 16}">${esc(base)}</text>`; }
        else if (a.y === b.y && l.id.startsWith('h0')) { seen.add(base); out += `<text class="road-label" x="${a.x + 8}" y="${a.y - 8}">${esc(base)}</text>`; }
        else if (a.y === b.y) { seen.add(base); out += `<text class="road-label" x="${a.x + 8}" y="${a.y - 9}">${esc(base)}</text>`; }
        else if (l.id.endsWith('_0')) { seen.add(base); out += `<text class="road-label" transform="translate(${a.x - 8},${a.y + 14}) rotate(90)">${esc(base)}</text>`; }
      });
    }

    // depots
    if (opts.showDepots) {
      world.depots.forEach((d) => {
        const n = N[d.node];
        const icon = { drain: '🛠', rescue: '🚑', police: '🚓' }[d.kind];
        out += `<g transform="translate(${n.x},${n.y})" pointer-events="none"><circle r="13" fill="#fff" stroke="#0B2545" stroke-width="2"/><text y="5" text-anchor="middle" font-size="13">${icon}</text></g>`;
      });
    }

    // reports
    if (opts.showReports && data.reports) {
      data.reports.forEach((r) => {
        const s = world.sectors[r.sector];
        if (!s) return;
        const x = s.x + 22 + hash(r.id) * (s.w - 44), y = s.y + 40 + hash(r.id + 'y') * (s.h - 62);
        const col = r.ai.floodEvidence >= 0.6 ? '#D7263D' : r.ai.floodEvidence >= 0.3 ? '#EE8434' : '#8FA3B8';
        out += `<g transform="translate(${x},${y})"><path d="M0,0 C-8,-10 -8,-20 0,-20 C8,-20 8,-10 0,0Z" fill="${col}" stroke="#fff" stroke-width="1.5"/><circle cy="-13" r="3" fill="#fff"/><title>${esc(r.name)} ${r.clock}: ${esc(r.text)}</title></g>`;
      });
    }

    // responders en route / on site
    if (data.tickets && data.simMin != null) {
      data.tickets.filter((t) => t.status !== 'Resolved').forEach((t) => {
        const pos = ticketPosition(world, t, data.simMin);
        if (!pos) return;
        const icon = t.kind === 'rescue' ? '🚑' : '🚛';
        out += `<g transform="translate(${pos.x},${pos.y})" pointer-events="none"><circle r="15" fill="#13A89E" stroke="#fff" stroke-width="2.5"/><text y="5" text-anchor="middle" font-size="14">${icon}</text></g>`;
      });
    }

    if (opts.pin) {
      out += `<g transform="translate(${opts.pin.x},${opts.pin.y})" pointer-events="none"><path d="M0,0 C-11,-14 -11,-28 0,-28 C11,-28 11,-14 0,0Z" fill="#0B2545" stroke="#fff" stroke-width="2"/><circle cy="-18" r="4.5" fill="#3FD0C1"/></g>`;
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
    (t.route || []).forEach((id) => {
      const l = world.links.find((x) => x.id === id);
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
      <span><i style="background:#2BB673"></i>free flow</span><span><i style="background:#E9B949"></i>slow</span>
      <span><i style="background:#EE8434"></i>congested</span><span><i style="background:#D7263D"></i>jammed</span>
      <span><i style="background:#7A0F1D"></i>gridlock / stalled</span><span><i style="background:repeating-linear-gradient(90deg,#5B6B7B 0 5px,transparent 5px 9px)"></i>closed by ITP</span>
    </div>`;
  }

  window.NikaasiMap = { render, legendHTML, ttiColor, esc, SEV_FILL };
})();
