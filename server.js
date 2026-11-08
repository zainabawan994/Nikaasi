'use strict';
/**
 * Nikaasi HTTP server — zero dependencies (Node ≥ 18).
 *   node server.js            → http://localhost:3000
 * Env: PORT, GEMINI_API_KEY, GEMINI_MODEL, AUTHORITY_PIN (default 1122), TICK_MS
 */
const http = require('http');
const fs = require('fs');
const path = require('path');

loadDotEnv(path.join(__dirname, '.env'));
const { Engine } = require('./src/engine');
const { sectorFromLatLng, sectorFromXY } = require('./src/config');

const PORT = Number(process.env.PORT) || 3000;
const PIN = process.env.AUTHORITY_PIN || '1122';
const PUBLIC = path.join(__dirname, 'public');
const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.json': 'application/json' };

function createServer(engine = new Engine({ tickMs: Number(process.env.TICK_MS) || 1500 })) {
  const clients = new Set(); // {res, authority}
  let pending = false;
  engine.on('state', () => {
    if (pending) return; // coalesce bursts into one push
    pending = true;
    setImmediate(() => {
      pending = false;
      if (!clients.size) return;
      const auth = `data: ${JSON.stringify(engine.state({ authority: true }))}\n\n`;
      const pub = `data: ${JSON.stringify(engine.state({ authority: false }))}\n\n`;
      clients.forEach((c) => c.res.write(c.authority ? auth : pub));
    });
  });

  const routes = {
    'GET /api/config': () => ({ world: publicWorld(engine.world), ai: engine.state({ authority: false }).ai }),
    'GET /api/state': (req) => engine.state({ authority: isAuthority(req) }),
    'GET /api/feeds/weather': () => ({ source: 'PMD Islamabad (mock feed)', observedAt: engine.clock(), sectors: engine.weather }),
    'GET /api/feeds/traffic': () => engine.trafficFeed(),
    'POST /api/reports': async (req, body) => {
      let sector = body.sector;
      if (!sector && Number.isFinite(body.lat)) sector = sectorFromLatLng(engine.world, body.lat, body.lng);
      if (!sector && Number.isFinite(body.x)) sector = sectorFromXY(engine.world, body.x, body.y);
      return engine.submitReport({ ...body, sector, bot: false });
    },
    'GET /api/alerts': (req, _b, url) => {
      const s = url.searchParams.get('sector');
      return engine.broadcasts.filter((b) => !s || b.sectors.includes(s)).slice(-20).reverse();
    },
    'POST /api/auth': (req, body) => {
      if (String(body.pin) !== PIN) throw Object.assign(new Error('Wrong PIN'), { status: 401 });
      return { ok: true };
    },
    'POST /api/scenario': auth((req, body) => {
      const a = body.action;
      if (a === 'play') engine.playScenario();
      else if (a === 'reset') engine.reset();
      else if (a === 'pause') engine.stop();
      else if (a === 'resume') engine.start();
      else if (a === 'step') engine.tick();
      else if (a === 'speed') engine.setSpeed(body.speed);
      else throw Object.assign(new Error('Unknown action'), { status: 400 });
      engine.emit('state');
      return { ok: true, running: engine.running, speed: engine.speed };
    }),
    'POST /api/weather': auth((req, body) => { engine.setWeatherOverride(body.sector, body.alert, body.rainMmHr); return { ok: true }; }),
    'POST /api/settings': auth((req, body) => {
      if (typeof body.autoExecute === 'boolean') engine.settings.autoExecute = body.autoExecute;
      engine.emit('state');
      return engine.settings;
    }),
  };

  function isAuthority(req) { return req.headers['x-authority-pin'] === PIN; }
  function auth(fn) {
    return (req, ...rest) => {
      if (!isAuthority(req)) throw Object.assign(new Error('Authority PIN required'), { status: 401 });
      return fn(req, ...rest);
    };
  }

  return http.createServer(async (req, res) => {
    const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
    try {
      if (url.pathname === '/api/stream') {
        res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', connection: 'keep-alive' });
        const c = { res, authority: url.searchParams.get('pin') === PIN };
        clients.add(c);
        res.write(`data: ${JSON.stringify(engine.state({ authority: c.authority }))}\n\n`);
        const ka = setInterval(() => res.write(': ka\n\n'), 20000);
        req.on('close', () => { clearInterval(ka); clients.delete(c); });
        return;
      }
      let m;
      if ((m = /^\/api\/reports\/(R-\d+)\/photo$/.exec(url.pathname)) && req.method === 'GET') {
        const p = engine.photos.get(m[1]);
        if (!p) return send(res, 404, { error: 'No photo' });
        const [, mime, b64] = /^data:([^;]+);base64,(.*)$/.exec(p);
        res.writeHead(200, { 'content-type': mime, 'cache-control': 'max-age=3600' });
        return res.end(Buffer.from(b64, 'base64'));
      }
      if ((m = /^\/api\/incidents\/(NKS-\d+)\/execute$/.exec(url.pathname)) && req.method === 'POST') {
        if (!isAuthority(req)) return send(res, 401, { error: 'Authority PIN required' });
        const inc = engine.execute(m[1], 'duty officer');
        return send(res, 200, { ok: true, id: inc.id, status: inc.status });
      }
      if ((m = /^\/api\/incidents\/(NKS-\d+)\/reassess$/.exec(url.pathname)) && req.method === 'POST') {
        if (!isAuthority(req)) return send(res, 401, { error: 'Authority PIN required' });
        const inc = engine.incidents.find((i) => i.id === m[1]);
        if (!inc) return send(res, 404, { error: 'Not found' });
        if (engine.aiCache[inc.sector]) engine.aiCache[inc.sector].lastCallMs = 0;
        engine.requestAI(inc.sector, 'manual');
        return send(res, 202, { ok: true, aiEnabled: engine.state({ authority: false }).ai.enabled });
      }
      const route = routes[`${req.method} ${url.pathname}`];
      if (route) {
        const body = req.method === 'POST' ? await readJSON(req) : {};
        return send(res, 200, await route(req, body, url));
      }
      if (url.pathname.startsWith('/api/')) return send(res, 404, { error: 'Not found' });
      return serveStatic(url.pathname, res);
    } catch (e) {
      send(res, e.status || 500, { error: e.status ? e.message : 'Internal error' });
      if (!e.status) console.error(e);
    }
  });
}

function publicWorld(w) {
  return { nodes: w.nodes, sectors: w.sectors, links: w.links.map(({ id, a, b, name, kind, underpass, sectors }) => ({ id, a, b, name, kind, underpass, sectors })), depots: w.depots, size: w.size };
}

function readJSON(req) {
  return new Promise((resolve, reject) => {
    let size = 0; const chunks = [];
    req.on('data', (c) => {
      size += c.length;
      if (size > 3_000_000) { reject(Object.assign(new Error('Payload too large'), { status: 413 })); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => {
      if (!chunks.length) return resolve({});
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); } catch { reject(Object.assign(new Error('Invalid JSON'), { status: 400 })); }
    });
    req.on('error', reject);
  });
}

function send(res, status, obj) {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
  res.end(JSON.stringify(obj));
}

function serveStatic(p, res) {
  if (p === '/') p = '/index.html';
  const file = path.normalize(path.join(PUBLIC, decodeURIComponent(p)));
  if (!file.startsWith(PUBLIC)) return send(res, 403, { error: 'Forbidden' });
  fs.readFile(file, (err, data) => {
    if (err) return send(res, 404, { error: 'Not found' });
    res.writeHead(200, { 'content-type': MIME[path.extname(file)] || 'application/octet-stream' });
    res.end(data);
  });
}

function loadDotEnv(file) {
  if (!fs.existsSync(file)) return;
  fs.readFileSync(file, 'utf8').split(/\r?\n/).forEach((line) => {
    const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/.exec(line);
    if (m && !(m[1] in process.env)) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
  });
}

if (require.main === module) {
  const server = createServer();
  server.listen(PORT, () => {
    console.log(`Nikaasi running → http://localhost:${PORT}`);
    console.log(`  Citizen app:        http://localhost:${PORT}/citizen.html`);
    console.log(`  Authority dashboard: http://localhost:${PORT}/authority.html  (PIN ${PIN === '1122' ? '1122' : 'from AUTHORITY_PIN'})`);
    console.log(`  AI engine: ${process.env.GEMINI_API_KEY ? `Gemini (${process.env.GEMINI_MODEL || 'gemini-3.5-flash'}) + rules-v1` : 'rules-v1 (set GEMINI_API_KEY to enable Gemini)'}`);
  });
}

module.exports = { createServer };
