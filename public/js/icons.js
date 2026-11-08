/* Line icons (24px grid, stroke = currentColor). No emoji anywhere in the UI. */
(function () {
  const P = {
    dashboard: '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>',
    flood: '<path d="M12 3l9.5 17h-19z"/><path d="M12 10v4.5"/><path d="M12 17.5h.01"/>',
    incidents: '<path d="M4 20V11M10 20V5M16 20v-6M2 20h20"/>',
    map: '<path d="M9 4L3 6.5v14L9 18l6 2.5 6-2.5V4l-6 2.5z"/><path d="M9 4v14M15 6.5v14"/>',
    truck: '<path d="M2 6h12v10H2z"/><path d="M14 9h4l3 3.5V16h-7"/><circle cx="6" cy="17.5" r="1.8"/><circle cx="17.5" cy="17.5" r="1.8"/>',
    bell: '<path d="M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    settings: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8"/><path d="M18 14.3c2.3.7 3.5 2.8 3.5 5.7"/>',
    waves: '<path d="M2 8c2-2 4-2 6 0s4 2 6 0 4-2 6 0"/><path d="M2 13c2-2 4-2 6 0s4 2 6 0 4-2 6 0"/><path d="M2 18c2-2 4-2 6 0s4 2 6 0 4-2 6 0"/>',
    rain: '<path d="M7 15a4 4 0 1 1 .9-7.9A5.2 5.2 0 0 1 18 8.2 3.4 3.4 0 0 1 18 15z"/><path d="M8.5 18l-1 3M12.5 18l-1 3M16.5 18l-1 3"/>',
    car: '<path d="M3 13.5l2-5.2A2 2 0 0 1 6.9 7h10.2a2 2 0 0 1 1.9 1.3l2 5.2V18h-3v-2H6v2H3z"/><path d="M3 13.5h18"/><circle cx="7.5" cy="13.5" r=".6"/><circle cx="16.5" cy="13.5" r=".6"/>',
    shield: '<path d="M12 3l8 3v6c0 5-3.5 8.2-8 9-4.5-.8-8-4-8-9V6z"/><path d="M8.5 12.2l2.4 2.4 4.6-5"/>',
    check: '<path d="M5 12.5l4.2 4.2L19 7"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/>',
    bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    radio: '<circle cx="12" cy="12" r="1.8"/><path d="M8 8a5.7 5.7 0 0 0 0 8M16 8a5.7 5.7 0 0 1 0 8M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14"/>',
    route: '<circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M8 19h7.5a3.5 3.5 0 0 0 0-7h-7a3.5 3.5 0 0 1 0-7H16"/>',
    megaphone: '<path d="M3 10.5v3a1 1 0 0 0 1 1h2.5L13 19V5L6.5 9.5H4a1 1 0 0 0-1 1z"/><path d="M16.5 9a4 4 0 0 1 0 6M19 6.5a7.5 7.5 0 0 1 0 11"/>',
    person: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7"/>',
    logout: '<path d="M15 4h4v16h-4"/><path d="M10 8l-4 4 4 4"/><path d="M6 12h10"/>',
    play: '<path d="M7 5l12 7-12 7z"/>',
    pause: '<path d="M8 5v14M16 5v14"/>',
    step: '<path d="M6 5l9 7-9 7z"/><path d="M18 5v14"/>',
    reset: '<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4v4.5h4.5"/>',
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    pin: '<path d="M12 21.5s7-7.3 7-12.5a7 7 0 0 0-14 0c0 5.2 7 12.5 7 12.5z"/><circle cx="12" cy="9" r="2.5"/>',
    medic: '<rect x="3" y="7" width="18" height="11" rx="2"/><path d="M12 9.5v6M9 12.5h6"/><path d="M9 7V5h6v2"/>',
    photo: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10.5" r="1.8"/><path d="M21 16.5l-5-5-9 8"/>',
    brain: '<path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5.2A3 3 0 0 0 6 17a3 3 0 0 0 3 3h0V4z"/><path d="M15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5.2A3 3 0 0 1 18 17a3 3 0 0 1-3 3V4z"/>',
    layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    gauge: '<path d="M4 18a8 8 0 1 1 16 0"/><path d="M12 18l4-6"/>',
    db: '<ellipse cx="12" cy="5.5" rx="7.5" ry="2.8"/><path d="M4.5 5.5v13c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8v-13"/><path d="M4.5 12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8"/>',
  };
  function icon(name, cls = '') {
    return `<svg class="ic ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[name] || ''}</svg>`;
  }
  window.NikaasiIcons = { icon };
})();
