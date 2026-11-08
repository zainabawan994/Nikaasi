# Nikaasi — *Pani se pehle, plan.*

AI flood signal-fusion and coordinated-response simulator for Islamabad, built for the **Nikaas** challenge.

Nikaasi combines three signals that usually live in separate systems:
- **citizen flood reports** (English / Roman Urdu, optional photo)
- a **rainfall-alert feed** (mock PMD)
- a **traffic-congestion feed** (mock ITP)

It judges **how serious** the flooding really is, with a confidence level and a plain-language explanation. It then **plans and simulates** a coordinated response: closing and rerouting flooded roads, dispatching CDA drain crews and Rescue 1122, and alerting residents. The authority dashboard shows the zone **before, after, and what would have happened with no response**.

## Run it
There are two interchangeable backends. Both serve the same API and the same frontend in `public/`.

### Python / FastAPI backend (recommended)
Requires **Python 3.10+**.
```bash
cd nikaasi/backend
pip install -r requirements.txt
python run.py            # http://localhost:3000  ·  API docs: http://localhost:3000/docs
python -m pytest         # 8 tests
```
| File | Role |
|---|---|
| `backend/app/main.py` | FastAPI routes, Pydantic schemas, PIN dependency, SSE stream, static frontend |
| `backend/app/engine.py` | Async simulation clock, incidents, execution, tickets, alerts |
| `backend/app/fusion.py` · `nlp.py` · `gemini.py` | Severity rule, Roman Urdu interpreter, Google AI Studio adapter (httpx) |
| `backend/app/planner.py` · `traffic.py` · `config.py` · `scenario.py` | Response planner, traffic model, world map, demo script |

All routes are `async def`, so the engine and its tick loop stay on the event-loop thread.

### Node backend (original prototype)
Requires **Node.js 18+**, with no dependencies.
```bash
cd nikaasi
npm start                # http://localhost:3000
```
| Screen | URL |
|---|---|
| Landing | `/` |
| Citizen app | `/citizen.html` |
| Authority dashboard | `/authority.html` (demo PIN **1122**) |

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
There are 8 tests: report understanding, every fusion rule property (a single report can't trigger a response, duplicates count once, corroboration, expiry), the **full end-to-end story** (detect → plan → execute → measurable improvement → crews drain → resolve → roads reopen), and the HTTP API, including the PIN guard.

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
- Alerts and tickets are simulated (no SMS gateway, no CDA system). State lives in memory and resets on restart.
- The authority PIN is a demo gate, not real authentication.
