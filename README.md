# Nikaasi — *Pani se pehle, plan.*

AI flood signal-fusion and coordinated-response simulator for Islamabad, built for the **Nikaas** challenge.

Nikaasi combines three signals that usually live in separate systems:
- **citizen flood reports** (English / Roman Urdu, optional photo)
- a **rainfall-alert feed** (mock PMD)
- a **traffic-congestion feed** (mock ITP)

It judges **how serious** the flooding really is, with a confidence level and a plain-language explanation. It then **plans and simulates** a coordinated response: closing and rerouting flooded roads, dispatching CDA drain crews and Rescue 1122, and alerting residents. The authority dashboard shows the zone **before, after, and what would have happened with no response**.

## Run it
There are two interchangeable backends. Both serve the same API and the same frontend in `public/`.

### Python / FastAPI backend (the full-stack app)
Requires **Python 3.10+** and, for persistence, **PostgreSQL 14+**.
```bash
cd nikaasi/backend
pip install -r requirements.txt
python run.py            # http://localhost:3000  ·  API docs: http://localhost:3000/docs (development only)
python -m pytest         # 22 tests (+3 PostgreSQL tests, see below)
```
The dashboard needs an account. Set `ADMIN_EMAIL` and `ADMIN_PASSWORD` in `nikaasi/.env` before the first start, or run `python -m app.cli create-user you@example.org --role admin`. Then sign in at `/login.html`. Admins manage users and see the audit log at `/admin.html`. See **[SECURITY.md](SECURITY.md)** for the full security model.

### Deploy online for free
Step-by-step guide for an always-on public server with HTTPS, PostgreSQL and daily backups on Oracle Cloud Always Free: **[DEPLOY.md](DEPLOY.md)** (uses `deploy/compose.yml`, `deploy/Caddyfile` and `deploy/setup-server.sh`).

### Docker (app + PostgreSQL, local)
```bash
cp .env.docker.example .env.docker    # fill in POSTGRES_PASSWORD, ADMIN_EMAIL, ADMIN_PASSWORD
docker compose --env-file .env.docker up -d --build
```
The app runs as a non-root, read-only container on `127.0.0.1:3000`. The database is only reachable from the app container. Put an HTTPS reverse proxy in front for public access.
| File | Role |
|---|---|
| `backend/app/main.py` | FastAPI routes, Pydantic schemas, auth dependencies, SSE stream, static frontend |
| `backend/app/auth.py` · `security.py` · `cli.py` | Users, sessions, audit log · hashing, rate limits, headers, CSRF, upload checks · admin CLI |
| `backend/migrations/` | Alembic schema migrations |
| `backend/app/engine.py` | Async simulation clock, incidents, execution, tickets, alerts |
| `backend/app/fusion.py` · `nlp.py` · `gemini.py` | Severity rule, Roman Urdu interpreter, Google AI Studio adapter (httpx) |
| `backend/app/planner.py` · `traffic.py` · `config.py` · `scenario.py` | Response planner, traffic model, world map, demo script |
| `backend/app/db.py` | PostgreSQL schema and write-behind store (SQLAlchemy + psycopg2) |

All routes are `async def`, so the engine and its tick loop stay on the event-loop thread.

### PostgreSQL database (FastAPI backend)
With `DATABASE_URL` set in `nikaasi/.env`, everything the system records is saved to PostgreSQL. Without it, or if the server can't be reached, the app runs in memory as before.

1. Create a dedicated role and database (PostgreSQL 14+). Don't run the app as `postgres`:
   ```bash
   psql -U postgres -c "CREATE ROLE nikaasi_app LOGIN PASSWORD 'long-random-password'"
   createdb -U postgres -O nikaasi_app nikaasi
   ```
2. Add it to `nikaasi/.env`:
   ```
   DATABASE_URL=postgresql://nikaasi_app:long-random-password@localhost:5432/nikaasi
   ```
3. Start the server. Alembic migrations create and upgrade the tables automatically.

| Table | Contents |
|---|---|
| `runs` | One row per demo session (each Play / Reset); every other row has its `run_id` |
| `reports` | Citizen reports: text, sector, AI reading (`ai` JSONB), hazard, GPS, photo (`bytea`) |
| `incidents` | One row per incident: status, severity, peak, score, explanation, response `plan` and before/after `impact` (JSONB) |
| `incident_trail` | The reasoning trail, one row per step, with a score snapshot |
| `tickets` | Dispatch tickets with status and full `history` |
| `broadcasts` | Resident alerts (English + Roman Urdu), recipients, delivered, failed |
| `events` | The activity log |
| `users` · `sessions` · `audit_log` | Accounts (bcrypt hashes), login sessions (token hashes only), security audit trail |

Writes go through a background thread, so the simulation never waits on the database. History is available to signed-in users (`/api/db/status` is admin-only):

| Endpoint | Returns |
|---|---|
| `GET /api/db/status` | Connection status and row counts |
| `GET /api/history/runs` | Past runs with report / incident counts |
| `GET /api/history/incidents?run=` | Incidents (optionally for one run) |
| `GET /api/history/incidents/{run}/{code}/trail` | Stored reasoning trail |
| `GET /api/history/reports?run=&sector=` | Stored reports |

Database tests use a separate, throwaway database. Their tables are dropped and recreated each run:
```bash
createdb -U postgres nikaasi_test
set NIKAASI_TEST_DATABASE_URL=postgresql://postgres:YOUR_POSTGRES_PASSWORD@localhost:5432/nikaasi_test
python -m pytest
```

### Node backend (original prototype)
Requires **Node.js 18+**, with no dependencies.
```bash
cd nikaasi
npm start                # http://localhost:3000
```

Press **▶ Play "G-10 monsoon" story** on the dashboard. See [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) for the walkthrough.

### Turn on Gemini (Google AI Studio)
1. Get a key at https://aistudio.google.com/apikey
2. Copy `.env.example` to `.env` and set `GEMINI_API_KEY=...` (optionally `GEMINI_MODEL`).
3. Restart. The dashboard badge changes to `AI: gemini-… + rules-v1`.

Gemini then reads every citizen report, including the photo, and re-judges each incident. Its score is blended with the deterministic rule engine and **clamped to ±15 points**, and both are shown. Without a key, or if the call fails or times out, everything runs on the offline engines, so a demo never breaks on bad Wi-Fi.

## Test (Node)
```bash
npm test
```
There are 8 tests: report understanding, every fusion rule property (a single report can't trigger a response, duplicates count once, corroboration, expiry), the **full end-to-end story** (detect → plan → execute → measurable improvement → crews drain → resolve → roads reopen), and the HTTP API. The Node prototype still uses a demo PIN; only the FastAPI backend has accounts.

## Checkpoint deliverables
| Checkpoint | Where |
|---|---|
| 1 · User roles, data flow, severity rule | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| 1 · Name, tagline, palette, logo | [`docs/BRANDING.md`](docs/BRANDING.md), [`public/assets/logo.svg`](public/assets/logo.svg) |
| 2 · Google Stitch screens | prompts in [`docs/STITCH_PROMPTS.md`](docs/STITCH_PROMPTS.md) (run them in Stitch) |
| 2 · Architecture diagram | [`docs/architecture.drawio`](docs/architecture.drawio) (open in draw.io) + Mermaid version in ARCHITECTURE.md |
| 3 · Citizen flow + mock feeds | `public/citizen.html`, `GET /api/feeds/weather`, `GET /api/feeds/traffic` |
| 3 · AI fusion (Google AI Studio) | `src/fusion.js`, `src/gemini.js`, `src/nlp.js` |
| 3 · Response planning + simulation | `src/planner.js`, `src/engine.js`, `src/traffic.js` |
| 3 · Authority dashboard | `public/authority.html` |
| 4 · End-to-end test + demo | `test/e2e.test.js`, `docs/DEMO_SCRIPT.md` |

## Honest limits
- The map is a **schematic** of 12 sectors, not real road geometry. Sector centres are approximate and only used to snap a GPS fix.
- Weather and traffic feeds are **simulated**. They come from the same world model, so they stay consistent with each other and react to the response.
- Alerts and tickets are simulated (no SMS gateway, no CDA system).
- With PostgreSQL, records are kept across restarts, but a restart still begins a fresh simulation: past runs are history, not resumed. The Node backend keeps everything in memory.
- The Node prototype only has a shared demo PIN and no accounts. Use the FastAPI backend for anything beyond a local demo.
