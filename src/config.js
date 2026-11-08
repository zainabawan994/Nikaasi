'use strict';
/**
 * Static world model: a schematic (not to scale) map of 12 Islamabad sectors
 * (F/G/H rows × 11/10/9/8 columns) with an arterial road grid, a local
 * "markaz" node inside every sector, depots for response teams and a mock
 * resident registry used for alerts.
 */

const COLS = ['11', '10', '9', '8']; // west -> east
const ROWS = ['F', 'G', 'H'];        // north -> south

const ORIGIN_X = 60, ORIGIN_Y = 50, CELL_W = 170, CELL_H = 150;

// Approximate real-world centres (used only to snap a phone GPS fix to a sector)
const SECTOR_LATLNG = {
  'F-11': [33.6845, 72.9880], 'F-10': [33.6950, 73.0120], 'F-9': [33.7020, 73.0270], 'F-8': [33.7110, 73.0400],
  'G-11': [33.6680, 72.9980], 'G-10': [33.6760, 73.0150], 'G-9': [33.6850, 73.0310], 'G-8': [33.6960, 73.0480],
  'H-11': [33.6500, 73.0080], 'H-10': [33.6600, 73.0250], 'H-9': [33.6700, 73.0480], 'H-8': [33.6820, 73.0640],
};

// Mock runoff / drainage characteristics. G-10 has a partially blocked nullah
// and a low underpass — this is the "ground truth" the AI does NOT see directly.
const SECTOR_HYDRO = {
  'G-10': { runoff: 1.0, drain: 0.25 },
  'G-11': { runoff: 0.35, drain: 0.9 },
  'F-10': { runoff: 0.4, drain: 0.8 },
};
const DEFAULT_HYDRO = { runoff: 0.3, drain: 0.9 };

// Mock opt-in resident alert registry (subscribed phone numbers per sector)
const RESIDENTS = {
  'F-11': 860, 'F-10': 1420, 'F-9': 120, 'F-8': 980,
  'G-11': 1310, 'G-10': 1745, 'G-9': 1590, 'G-8': 1180,
  'H-11': 540, 'H-10': 690, 'H-9': 410, 'H-8': 300,
};

const H_ROADS = ['Margalla Rd', 'Nazimuddin Rd', 'Srinagar Hwy', 'IJP Rd'];
const V_ROADS = ['Golra Rd', '11th Ave', 'Ibn-e-Sina Rd', '9th Ave', '7th Ave'];
// road class by line: [capacity veh/h per direction, free-flow km/h]
const H_CLASS = [[1600, 50], [1800, 50], [4200, 80], [2200, 60]];
const V_CLASS = [[1400, 50], [1500, 50], [1800, 50], [2400, 60], [1800, 50]];

function buildWorld() {
  const nodes = {};
  const sectors = {};
  const links = []; // undirected roads; traffic model makes them directed

  // grid corner nodes
  for (let i = 0; i <= COLS.length; i++) {
    for (let j = 0; j <= ROWS.length; j++) {
      const id = `n${i}_${j}`;
      nodes[id] = { id, x: ORIGIN_X + i * CELL_W, y: ORIGIN_Y + j * CELL_H, kind: 'junction' };
    }
  }
  // sectors + markaz nodes
  ROWS.forEach((r, j) => {
    COLS.forEach((c, i) => {
      const id = `${r}-${c}`;
      const cx = ORIGIN_X + i * CELL_W + CELL_W / 2;
      const cy = ORIGIN_Y + j * CELL_H + CELL_H / 2;
      sectors[id] = {
        id, row: r, col: c, i, j,
        x: ORIGIN_X + i * CELL_W, y: ORIGIN_Y + j * CELL_H, w: CELL_W, h: CELL_H,
        cx, cy,
        latlng: SECTOR_LATLNG[id],
        hydro: SECTOR_HYDRO[id] || DEFAULT_HYDRO,
        residents: RESIDENTS[id] || 500,
        markaz: `m_${id}`,
      };
      nodes[`m_${id}`] = { id: `m_${id}`, x: cx, y: cy, kind: 'markaz', sector: id };
    });
  });

  const lenKm = (a, b) => Math.hypot(nodes[a].x - nodes[b].x, nodes[a].y - nodes[b].y) / CELL_W * 1.9;

  // horizontal arterials
  for (let j = 0; j <= ROWS.length; j++) {
    for (let i = 0; i < COLS.length; i++) {
      const a = `n${i}_${j}`, b = `n${i + 1}_${j}`;
      const [cap, speed] = H_CLASS[j];
      links.push({ id: `h${i}_${j}`, a, b, name: H_ROADS[j], cap, speed, km: lenKm(a, b), kind: 'arterial' });
    }
  }
  // vertical arterials
  for (let i = 0; i <= COLS.length; i++) {
    for (let j = 0; j < ROWS.length; j++) {
      const a = `n${i}_${j}`, b = `n${i}_${j + 1}`;
      const [cap, speed] = V_CLASS[i];
      links.push({ id: `v${i}_${j}`, a, b, name: V_ROADS[i], cap, speed, km: lenKm(a, b), kind: 'arterial' });
    }
  }
  // local sector streets (markaz <-> 4 corners)
  Object.values(sectors).forEach((s) => {
    [[s.i, s.j], [s.i + 1, s.j], [s.i, s.j + 1], [s.i + 1, s.j + 1]].forEach(([i, j], k) => {
      const a = s.markaz, b = `n${i}_${j}`;
      links.push({ id: `l_${s.id}_${k}`, a, b, name: `${s.id} local street`, cap: 700, speed: 30, km: lenKm(a, b), kind: 'local', sector: s.id });
    });
  });

  // The G-10 underpass on Srinagar Hwy (south edge of G-10)
  const underpass = links.find((l) => l.id === 'h1_2');
  underpass.name = 'Srinagar Hwy — G-10 Underpass';
  underpass.underpass = true;

  // which sectors each link borders
  links.forEach((l) => {
    l.sectors = l.sector ? [l.sector] : Object.values(sectors)
      .filter((s) => onSectorEdge(nodes[l.a], s) && onSectorEdge(nodes[l.b], s))
      .map((s) => s.id);
  });

  const depots = [
    { id: 'DEP-H9', name: 'CDA Drain Depot H-9', node: 'n3_3', team: 'CDA Drain Team 3', kind: 'drain' },
    { id: 'DEP-F8', name: 'CDA Drain Depot F-8', node: 'n4_0', team: 'CDA Drain Team 1', kind: 'drain' },
    { id: 'DEP-1122', name: 'Rescue 1122 Station G-9', node: 'n2_1', team: 'Rescue 1122 Unit 7', kind: 'rescue' },
    { id: 'DEP-ITP', name: 'ITP Sector Office G-11', node: 'n0_2', team: 'ITP Patrol 12', kind: 'police' },
  ];

  return { nodes, sectors, links, depots, cols: COLS, rows: ROWS, size: { w: ORIGIN_X * 2 + CELL_W * COLS.length, h: ORIGIN_Y * 2 + CELL_H * ROWS.length } };
}

function onSectorEdge(n, s) {
  const inX = n.x >= s.x - 0.1 && n.x <= s.x + s.w + 0.1;
  const inY = n.y >= s.y - 0.1 && n.y <= s.y + s.h + 0.1;
  const onBorder = Math.abs(n.x - s.x) < 0.1 || Math.abs(n.x - s.x - s.w) < 0.1 || Math.abs(n.y - s.y) < 0.1 || Math.abs(n.y - s.y - s.h) < 0.1;
  return inX && inY && onBorder;
}

/** Snap a GPS fix to the nearest sector centre. */
function sectorFromLatLng(world, lat, lng) {
  let best = null, bestD = Infinity;
  Object.values(world.sectors).forEach((s) => {
    const d = Math.hypot(s.latlng[0] - lat, (s.latlng[1] - lng) * Math.cos(lat * Math.PI / 180));
    if (d < bestD) { bestD = d; best = s.id; }
  });
  return bestD < 0.03 ? best : null; // ~3 km
}

/** Map a click on the schematic map to a sector. */
function sectorFromXY(world, x, y) {
  const s = Object.values(world.sectors).find((q) => x >= q.x && x <= q.x + q.w && y >= q.y && y <= q.y + q.h);
  return s ? s.id : null;
}

module.exports = { buildWorld, sectorFromLatLng, sectorFromXY };
