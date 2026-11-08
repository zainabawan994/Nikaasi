"""
Nikaasi engine. Every simulated minute it runs:
  1. scenario script  -> weather "truth" (storm)             [mock PMD feed]
  2. hydrology        -> hidden ground-truth water depth
  3. traffic model    -> congestion per road / sector         [mock ITP feed]
  4. signal fusion    -> per-sector severity + confidence     [rules-v1 (+ Gemini)]
  5. incident manager -> open / escalate / plan / auto-execute
  6. response sim     -> tickets progress, crews pump water, closures, alerts
"""
from __future__ import annotations

import asyncio
import copy
import logging
import math
import os
import random
import time
from datetime import datetime, timedelta, timezone

from . import fusion, gemini, nlp, planner, traffic, weather_live
from .config import build_world, neighbours
from .db import Store
from .fusion import RANK
from .scenario import G10_MONSOON

log = logging.getLogger("nikaasi")

START_CLOCK_MIN = 18 * 60 + 40  # 18:40 PKT (demo story clock)
PKT = timezone(timedelta(hours=5))  # Pakistan has no daylight saving
LIVE_TICK_S = 60                 # live mode: one simulated minute per real minute
LIVE_STALE_MIN = 30              # ignore live rain data older than this
PMD_ALERT_HOURS = 6              # officer-entered PMD alerts expire after this
PUMP_RATE = 5                   # cm/min removed by a drain crew on site
MAX_DEPTH = 60


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def clamp(x: float, a: float, b: float) -> float:
    return max(a, min(b, x))


class Engine:
    def __init__(self, tick_ms: int = 1500, seed: int = 7, store: Store | None = None, weather_source: str = "demo"):
        self.store = store or Store(None)  # Store(None) = in-memory only
        self.default_mode = "live" if weather_source == "live" else "demo"
        # real-world weather state survives simulation resets
        self.live = {"data": {}, "ok": False, "fetchedAt": None, "error": None}
        self.pmd_alerts: dict[str, dict] = {}
        self._poller: asyncio.Task | None = None
        self.world = build_world()
        self.base_tick_ms = tick_ms
        self.seed = seed
        self.listeners: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self._bg: set[asyncio.Task] = set()
        self.reset()

    # ------------------------------------------------------------ lifecycle
    def reset(self, mode: str | None = None) -> None:
        self.stop()
        self.mode = mode or self.default_mode  # "live" = real weather, real time; "demo" = scripted story
        self.rng = random.Random(self.seed)
        self.sim_min = 0
        self.speed = 1.0
        self.scenario: dict | None = None
        self.storm = {"center": "G-10", "peak": 0, "alerts": {}}
        self.manual_weather: dict[str, dict] = {}
        sec = self.world["sectors"]
        self.depth = {s: 0.0 for s in sec}
        self.shadow_depth = {s: 0.0 for s in sec}
        self.pumping = {s: 0.0 for s in sec}
        self.drain_fixed = {s: 0.0 for s in sec}
        self.reports: list[dict] = []
        self.photos: dict[str, str] = {}
        self.incidents: list[dict] = []
        self.tickets: list[dict] = []
        self.broadcasts: list[dict] = []
        self.events: list[dict] = []
        self.closed: set[str] = set()
        self.informed: set[str] = set()
        self.settings = {"autoExecute": False}
        self.ai_cache: dict[str, dict] = {}
        self.weather: dict[str, dict] = {}
        self.baseline = traffic.simulate(self.world, {})
        self.traffic_now = self.baseline
        self.shadow: dict | None = None
        self.traffic_history = {s: [] for s in sec}
        self.assessments: dict[str, dict] = {}
        self.seq = {"inc": 0, "ticket": 1040, "rep": 0, "bc": 0}
        self.store.new_run()  # every reset / play is a separate run in PostgreSQL
        self.update_weather()
        self.record_traffic()
        self.fuse_all()
        if self.mode == "live":
            self.log("system", f"Nikaasi online (LIVE). Rain: {weather_live.PROVIDER} live data; PMD alerts: entered by duty officer; traffic: simulated.", persist=False)
        else:
            self.log("system", "Nikaasi online (DEMO). Feeds: citizen reports, PMD weather (mock), ITP traffic (mock).", persist=False)
        self.emit()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.get_running_loop().create_task(self._run())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
        self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(LIVE_TICK_S if self.mode == "live" else max(0.15, self.base_tick_ms / 1000 / self.speed))
            try:
                self.tick()
            except Exception:  # keep the clock alive; log and continue
                log.exception("tick failed")

    def set_speed(self, x) -> None:
        try:
            self.speed = clamp(float(x), 0.5, 8)
        except (TypeError, ValueError):
            self.speed = 1.0

    def go_live(self) -> None:
        """Real-time mode with live rainfall (the default in production)."""
        self.reset(mode="live")
        self.start()
        self.start_weather_poller()
        self._spawn(self.refresh_live_weather())

    def start_weather_poller(self) -> None:
        if self._poller and not self._poller.done():
            return
        self._poller = asyncio.get_running_loop().create_task(self._poll_weather())

    async def _poll_weather(self) -> None:
        interval = max(60, int(os.environ.get("WEATHER_REFRESH_S", "300")))
        while True:
            if self.mode == "live":
                await self.refresh_live_weather()
            await asyncio.sleep(interval if self.mode == "live" else 30)

    async def refresh_live_weather(self) -> None:
        try:
            data = await weather_live.fetch(self.world["sectors"])
        except Exception as e:  # noqa: BLE001 - network problems must never stop the engine
            first_failure = self.live["ok"] or self.live["error"] is None
            self.live.update(ok=False, error=str(e)[:160])
            if first_failure:
                self.log("weather", f"Live weather unavailable ({str(e)[:80]}). Retrying automatically.")
            log.warning("live weather fetch failed: %s", e)
        else:
            was_down = not self.live["ok"] and self.live["fetchedAt"] is not None
            self.live.update(data=data, ok=True, error=None, fetchedAt=datetime.now(timezone.utc))
            if was_down:
                self.log("weather", "Live weather restored.")
        if self.mode == "live":
            self.update_weather()
            self.fuse_all()
            self.manage_incidents()
            self.emit()

    def play_scenario(self) -> None:
        self.reset(mode="demo")
        self.scenario = {"name": "G-10 monsoon evening", "startedAt": self.sim_min,
                         "script": [dict(s, done=False) for s in G10_MONSOON]}
        self.store.set_scenario(self.scenario["name"])
        self.log("scenario", 'Scenario started: "G-10 monsoon evening".')
        self.tick()
        self.start()

    # ------------------------------------------------------------ pub/sub
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        self.listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.listeners.discard(q)

    def emit(self) -> None:
        for q in list(self.listeners):
            if q.empty():
                q.put_nowait(True)  # coalesce bursts into one push

    def _spawn(self, coro) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return
        t = loop.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    # ------------------------------------------------------------ main tick
    def tick(self) -> None:
        self.sim_min += 1
        self.run_script()
        self.update_weather()
        self.update_hydrology()
        self.traffic_now = traffic.simulate(self.world, self.depth, self.closed, self.informed)
        self.shadow = traffic.simulate(self.world, self.shadow_depth) if any(i["execution"] for i in self.incidents) else None
        self.record_traffic()
        self.fuse_all()
        self.manage_incidents()
        self.progress_tickets()
        self.emit()

    def run_script(self) -> None:
        if not self.scenario:
            return
        rel = self.sim_min - self.scenario["startedAt"]
        for step in self.scenario["script"]:
            if step["done"] or step["at"] > rel:
                continue
            step["done"] = True
            if "storm" in step:
                self.storm = step["storm"]
            if "log" in step:
                self.log("weather", step["log"])
            if "report" in step:
                r = step["report"]
                self.ingest_report(sector=r["sector"], reporter_id=r["reporterId"], name=r["name"], text=r["text"],
                                   ai=nlp.interpret(r["text"]), bot=True)

    # ------------------------------------------------------------ weather feed
    def update_weather(self) -> None:
        if self.mode == "live":
            self._update_live_weather()
            return
        c = self.world["sectors"][self.storm["center"]]
        w = {}
        for s in self.world["sectors"].values():
            d = math.hypot(s["i"] - c["i"], s["j"] - c["j"])
            jitter = 1 + (self.rng.random() - 0.5) * 0.12
            rain = self.storm["peak"] * math.exp(-(d * d) / (2 * 1.1 * 1.1)) * jitter
            alert = "none"
            for lvl in ("yellow", "orange", "red"):
                if s["id"] in self.storm["alerts"].get(lvl, []):
                    alert = lvl
            m = self.manual_weather.get(s["id"])
            if m:
                alert, rain = m["alert"], m["rainMmHr"]
            w[s["id"]] = {
                "sector": s["id"], "alert": alert, "rainMmHr": round(rain, 1),
                "forecastMmHr": round(rain * (0.9 if self.storm["peak"] > 30 else 0.6), 1),
                "source": "Manual override (authority)" if m else "PMD Islamabad (mock feed)",
                "observedAt": self.clock(),
            }
        self.weather = w

    def _update_live_weather(self) -> None:
        now = datetime.now(timezone.utc)
        fetched = self.live["fetchedAt"]
        fresh = fetched is not None and now - fetched <= timedelta(minutes=LIVE_STALE_MIN)
        for sid in [k for k, v in self.pmd_alerts.items() if v["until"] <= now]:
            del self.pmd_alerts[sid]
            self.log("weather", f"PMD alert for {sid} expired.", sid)
        w = {}
        for sid in self.world["sectors"]:
            d = self.live["data"].get(sid) if fresh else None
            rain = d["rainMmHr"] if d else 0.0
            forecast = d["forecastMmHr"] if d else 0.0
            rain_alert = weather_live.alert_for(max(rain, forecast))
            pmd = self.pmd_alerts.get(sid)
            alert = weather_live.max_level(rain_alert, pmd["level"] if pmd else "none")
            sources = [f"{weather_live.PROVIDER} (live)" if d else "live rain unavailable"]
            if pmd:
                sources.append(f"PMD alert entered by {pmd['by']}")
            w[sid] = {
                "sector": sid, "alert": alert, "rainMmHr": rain, "forecastMmHr": forecast,
                "rainAlert": rain_alert, "pmdAlert": pmd["level"] if pmd else None,
                "source": " + ".join(sources),
                "observedAt": d["observedAt"] if d else None,
            }
        self.weather = w

    def update_hydrology(self) -> None:
        for s in self.world["sectors"].values():
            sid = s["id"]
            inflow = self.weather[sid]["rainMmHr"] / 60 * s["hydro"]["runoff"] * 4
            out = s["hydro"]["drain"] + self.drain_fixed[sid] + self.pumping[sid]
            self.depth[sid] = clamp(self.depth[sid] + inflow - out, 0, MAX_DEPTH)
            self.shadow_depth[sid] = clamp(self.shadow_depth[sid] + inflow - s["hydro"]["drain"], 0, MAX_DEPTH)

    def record_traffic(self) -> None:
        for s, h in self.traffic_history.items():
            noise = 1 + (self.rng.random() - 0.5) * 0.06
            h.append({"t": self.sim_min, "tti": round(self.traffic_now["sectors"][s]["tti"] * noise, 2)})
            del h[:-60]

    def traffic_feed(self) -> dict:
        links = {k: {"tti": v["tti"], "vc": v["vc"], "flow": v["flow"], "closed": v["closed"]} for k, v in self.traffic_now["links"].items()}
        sectors = {s: {"tti": h[-1]["tti"], "baseline": self.baseline["sectors"][s]["tti"], "history": h[-30:]}
                   for s, h in self.traffic_history.items()}
        return {"source": "ITP / navigation probe data (mock feed)", "observedAt": self.clock(), "sectors": sectors, "links": links}

    # ------------------------------------------------------------ citizen reports
    async def submit_report(self, *, sector: str | None, text: str, reporter_id: str | None = None, name: str | None = None,
                            photo: str | None = None, position: dict | None = None) -> dict:
        if not sector or sector not in self.world["sectors"]:
            raise HttpError(400, "Unknown sector")
        desc = str(text or "").strip()[:600]
        if len(desc) < 3:
            raise HttpError(400, "Please describe what you see")
        has_photo = isinstance(photo, str) and photo.startswith("data:image/")
        if has_photo and len(photo) > 2_000_000:
            raise HttpError(413, "Photo too large (max ~1.5 MB)")
        ai = nlp.interpret(desc, has_photo)
        if gemini.enabled():
            try:
                ai = {**(await gemini.interpret_report(desc, photo if has_photo else None)), "tags": ai["tags"]}
            except Exception as e:  # noqa: BLE001 - any failure falls back to the lexicon
                ai["engineNote"] = f"Gemini unavailable ({str(e)[:60]}), used offline lexicon"
        report = self.ingest_report(sector=sector, reporter_id=reporter_id, name=name, text=desc, ai=ai,
                                    photo=photo if has_photo else None, position=position)
        inc = self.active_incident(sector)
        return {"report": report, "sectorStatus": self.public_assessment(sector), "incidentId": inc["id"] if inc else None}

    def ingest_report(self, *, sector: str, reporter_id: str | None, name: str | None, text: str, ai: dict,
                      photo: str | None = None, position: dict | None = None, bot: bool = False) -> dict:
        self.seq["rep"] += 1
        rid = f"R-{self.seq['rep']:04d}"
        if photo:
            self.photos[rid] = photo
        report = {
            "id": rid, "sector": sector, "reporterId": str(reporter_id or rid)[:64], "name": str(name or "Resident")[:40],
            "text": text, "hasPhoto": bool(photo), "t": self.sim_min, "clock": self.clock(), "ai": ai, "bot": bot,
            "position": position,
        }
        self.reports.append(report)
        self.store.save_report(report, photo)
        short = text[:80] + ("…" if len(text) > 80 else "")
        self.log("report", f'{report["name"]} ({sector}): "{short}" → AI: {ai["summary"]} [evidence {ai["floodEvidence"]}]', sector)
        self.fuse_all()
        self.manage_incidents(reason=f"new report {rid}")
        self.emit()
        return report

    # ------------------------------------------------------------ fusion
    def fuse_all(self) -> None:
        for s in self.world["sectors"]:
            rules = fusion.assess(
                sector=s,
                reports=[r for r in self.reports if r["sector"] == s],
                weather=self.weather.get(s),
                traffic_history=self.traffic_history[s],
                traffic_baseline=self.baseline["sectors"][s]["tti"],
                now=self.sim_min,
            )
            self.explain_diversion(s, rules)
            self.assessments[s] = self.combine_with_ai(rules)

    def explain_diversion(self, s: str, rules: dict) -> None:
        """Traffic displaced by our own diversions (or a bigger flood next door) is not evidence of flooding here."""
        near = [i for i in self.incidents if i["status"] != "resolved" and i["sector"] != s and s in neighbours(self.world, i["sector"])]
        diverting = next((i for i in near if i["plan"] and any(a["type"] == "reroute" and a["status"] == "done" for a in i["plan"]["actions"])), None)
        bigger = next((i for i in near if RANK[i["severity"]] >= 2
                       and self.assessments.get(i["sector"], {}).get("final", {}).get("score", 0) > rules["score"]), None)
        src = diverting or bigger
        T = rules["signals"]["traffic"]
        if not src or T["score"] == 0:
            return
        removed = round(100 * fusion.WEIGHTS["traffic"] * T["score"] * 0.6)
        T["score"] = round(T["score"] * 0.4, 3)
        T["note"] = (f"partly explained by diversion from {src['sector']} ({src['id']})" if diverting
                     else f"likely spill-over from {src['sector']} ({src['id']}) on shared roads")
        bd = rules["breakdown"]
        bd["traffic"] = round(bd["traffic"] - removed, 1)
        rules["score"] = max(0, rules["score"] - removed)
        corr = fusion.corroboration_bonus(rules["signals"]["citizen"]["score"], rules["signals"]["weather"]["score"], T["score"])
        rules["score"] = max(0, rules["score"] - (bd["corroboration"] - corr))
        bd["corroboration"] = corr
        rules["severity"] = fusion.severity_of(rules["score"])
        rules["explanation"] += f" Traffic spike {T['note']}; discounted."

    def combine_with_ai(self, rules: dict) -> dict:
        cache = self.ai_cache.get(rules["sector"])
        final = {"score": rules["score"], "severity": rules["severity"], "confidence": rules["confidence"],
                 "explanation": rules["explanation"], "engine": "rules-v1"}
        if cache and cache.get("result") and self.sim_min - cache["simMin"] <= 10:
            ai = cache["result"]
            blended = round((rules["score"] + ai["score"]) / 2)
            # guard-rail: the AI may move the rule score by at most +-15
            final["score"] = int(clamp(blended, rules["score"] - 15, rules["score"] + 15))
            final["severity"] = fusion.severity_of(final["score"])
            final["confidence"] = ai["confidence"]
            final["explanation"] = ai["explanation"] or rules["explanation"]
            final["engine"] = f"{ai['engine']} + rules-v1"
            final["ai"] = {**ai, "ageMin": self.sim_min - cache["simMin"]}
        return {**rules, "final": final}

    def request_ai(self, sector: str, reason: str = "", force: bool = False) -> None:
        if not gemini.enabled():
            return
        c = self.ai_cache.setdefault(sector, {})
        now_ms = time.time() * 1000
        if c.get("pending") or (not force and now_ms - c.get("lastCallMs", 0) < 15000):
            return
        c["pending"] = True
        c["lastCallMs"] = now_ms
        rules = self.assessments[sector]
        sample = [{"text": r["text"], "ai": r["ai"]["summary"], "minutesAgo": self.sim_min - r["t"]}
                  for r in [r for r in self.reports if r["sector"] == sector][-5:]]

        async def run():
            try:
                result = await gemini.judge_incident(rules, sample)
                c["result"], c["simMin"] = result, self.sim_min
                inc = self.active_incident(sector)
                if inc:
                    self.trail(inc, "ai", f"Gemini judgment ({result['engine']}): {result['score']}/100, "
                                          f"{result['confidence']} confidence — {result['explanation']}")
                self.fuse_all()
                self.emit()
            except Exception as e:  # noqa: BLE001
                c["error"] = str(e)
                self.log("system", f"Gemini call failed for {sector}: {str(e)[:90]} (continuing with rules-v1)")
            finally:
                c["pending"] = False

        self._spawn(run())

    # ------------------------------------------------------------ incidents
    def active_incident(self, sector: str) -> dict | None:
        return next((i for i in self.incidents if i["sector"] == sector and i["status"] != "resolved"), None)

    def manage_incidents(self, reason: str | None = None) -> None:
        for sector, a in self.assessments.items():
            sev = a["final"]["severity"]
            inc = self.active_incident(sector)
            if not inc and RANK[sev] >= 1:
                self.seq["inc"] += 1
                inc = {
                    "id": f"NKS-{self.seq['inc']:04d}", "sector": sector, "status": "open",
                    "openedAt": self.sim_min, "openedClock": self.clock(), "severity": sev, "peakSeverity": sev,
                    "trail": [], "plan": None, "execution": None, "quietMin": 0, "planPending": False, "lastChange": None,
                    "assessment": a,
                }
                self.incidents.append(inc)
                self.log("incident", f"Incident {inc['id']} opened for {sector} — {sev} ({a['final']['score']}/100).", sector)
                self.trail(inc, "open", f"Opened: {a['final']['explanation']}", a)
                self.request_ai(sector, "opened")
            if not inc:
                continue
            inc["assessment"] = a
            prev = inc["severity"]
            if sev != prev:
                up = RANK[sev] > RANK[prev]
                # hysteresis: only de-escalate after 3 quiet minutes
                if up or inc["lastChange"] is None or self.sim_min - inc["lastChange"] >= 3:
                    inc["severity"] = sev
                    inc["lastChange"] = self.sim_min
                    if RANK[sev] > RANK[inc["peakSeverity"]]:
                        inc["peakSeverity"] = sev
                    word = "Escalated" if up else "De-escalated"
                    self.trail(inc, "escalate" if up else "deescalate",
                               f"{word} {prev} → {sev} ({a['final']['score']}/100). {a['final']['explanation']}", a)
                    self.log("incident", f"{inc['id']} {word.lower()} to {sev}.", sector)
                    self.request_ai(sector, "severity change")
            elif reason and reason.startswith("new report") and a["signals"]["citizen"]["count"]:
                self.trail(inc, "evidence", f"New evidence ({reason}). Score now {a['final']['score']}/100. {a['final']['explanation']}", a)
                self.request_ai(sector, reason)

            self._maybe_plan(inc, a)
            self._maybe_resolve(inc, a)

    def _maybe_plan(self, inc: dict, a: dict) -> None:
        if RANK[inc["severity"]] < 1:
            return
        observed = self.traffic_feed()["links"]
        escalated = not inc["plan"] or RANK[inc["severity"]] > RANK[inc["plan"]["severity"]]
        if inc["plan"] and inc["planPending"] and not inc["execution"] and not escalated:
            # keep a not-yet-executed plan current with the latest observations (silent refresh)
            p = planner.plan(self.world, {**a, "severity": inc["plan"]["severity"]}, observed, inc["plan"])
            if p:
                for x in p["actions"]:
                    x["status"] = "proposed"
                p["version"] = inc["plan"]["version"]
                inc["plan"] = p
        if not escalated:
            return
        p = planner.plan(self.world, {**a, "severity": inc["severity"]}, observed, inc["plan"])
        if not p:
            return
        done = set(inc["execution"]["actionsDone"]) if inc["execution"] else set()
        for x in p["actions"]:
            x["status"] = "done" if x["id"] in done and x["type"] not in ("reroute", "alert") else "proposed"
        inc["plan"] = p
        inc["planPending"] = any(x["status"] == "proposed" for x in p["actions"])
        self.trail(inc, "plan", f"Response plan v{p['version']} ({p['severity']}): {'; '.join(x['title'] for x in p['actions'])}.")
        if self.settings["autoExecute"]:
            self.execute(inc["id"], "auto")

    def _maybe_resolve(self, inc: dict, a: dict) -> None:
        if inc["status"] == "responding":
            open_tickets = [t for t in self.tickets if t["incidentId"] == inc["id"] and t["status"] != "Resolved"]
            inc["quietMin"] = inc["quietMin"] + 1 if a["final"]["score"] < 30 and not open_tickets else 0
            if inc["quietMin"] >= 3:
                self.resolve(inc)
        elif inc["status"] == "open" and a["final"]["score"] < 15 and self.sim_min - inc["openedAt"] > 10:
            inc["status"] = "resolved"
            inc["resolvedAt"] = self.sim_min
            self.trail(inc, "resolve", "Closed without action: signals subsided below threshold.")

    def execute(self, incident_id: str, by: str = "authority") -> dict:
        inc = next((i for i in self.incidents if i["id"] == incident_id), None)
        if not inc:
            raise HttpError(404, "Incident not found")
        if not inc["plan"]:
            raise HttpError(409, "No plan to execute yet")
        todo = [x for x in inc["plan"]["actions"] if x["status"] == "proposed"]
        if not todo:
            raise HttpError(409, "Plan already executed")

        if not inc["execution"]:
            tn = self.traffic_now
            inc["execution"] = {
                "executedAt": self.sim_min, "executedClock": self.clock(), "by": by, "actionsDone": [],
                "before": {
                    "sectorTTI": tn["sectors"][inc["sector"]]["tti"],
                    "sectors": {k: v["tti"] for k, v in tn["sectors"].items()},
                    "metrics": dict(tn["metrics"]),
                    "links": copy.deepcopy(tn["links"]),
                    "score": inc["assessment"]["final"]["score"],
                    "severity": inc["severity"],
                },
            }
            # the counterfactual world starts from the same water level
            self.shadow_depth = dict(self.depth)
        inc["status"] = "responding"
        for x in todo:
            if x["type"] == "reroute":
                self.closed.update(x["links"])
                self.informed.add(inc["sector"])
                self.log("traffic", f"ITP diversion active: {len(x['links'])} segment(s) closed around {inc['sector']}; traffic rerouted.", inc["sector"])
            elif x["type"] == "traffic_advisory":
                self.log("traffic", f"Advisory sent to ITP for {inc['sector']}.", inc["sector"])
            elif x["type"] == "dispatch":
                self.create_ticket(inc, x)
            elif x["type"] == "alert":
                self.send_broadcast(inc, x)
            x["status"] = "done"
            x["executedClock"] = self.clock()
            inc["execution"]["actionsDone"].append(x["id"])
        inc["planPending"] = False
        self.trail(inc, "execute", f"Plan v{inc['plan']['version']} executed by {by}: {'; '.join(x['title'] for x in todo)}.")
        # re-run traffic now so the dashboard shows the effect this tick
        self.traffic_now = traffic.simulate(self.world, self.depth, self.closed, self.informed)
        self.shadow = traffic.simulate(self.world, self.shadow_depth)
        self.emit()
        return inc

    def create_ticket(self, inc: dict, action: dict) -> None:
        rescue = action["id"] == "dispatch_rescue"
        self.seq["ticket"] += 1
        t = {
            "id": f"{'R1122' if rescue else 'CDA-DRN'}-{self.seq['ticket']}",
            "incidentId": inc["id"], "sector": inc["sector"], "team": action["team"], "priority": action["priority"],
            "kind": "rescue" if rescue else "drain", "etaMin": action["etaMin"], "route": action["route"],
            "status": "Created", "createdAt": self.sim_min, "history": [{"status": "Created", "clock": self.clock()}],
        }
        self.tickets.append(t)
        self.store.save_ticket(t)
        self.log("dispatch", f"Ticket {t['id']} ({t['priority']}) created → {t['team']}, ETA {t['etaMin']} min.", inc["sector"])

    def progress_tickets(self) -> None:
        for t in self.tickets:
            age = self.sim_min - t["createdAt"]

            def set_status(status: str, note: str | None = None, t=t) -> None:
                if t["status"] == status:
                    return
                t["status"] = status
                t["history"].append({"status": status, "clock": self.clock(), "note": note})
                self.store.save_ticket(t)
                self.log("dispatch", f"{t['id']}: {status}" + (f" — {note}" if note else ""), t["sector"])

            if t["status"] == "Created" and age >= 1:
                set_status("Assigned", f"{t['team']} acknowledged")
            elif t["status"] == "Assigned" and age >= 2:
                set_status("En route")
            elif t["status"] == "En route" and age >= 2 + t["etaMin"]:
                if t["kind"] == "drain":
                    self.pumping[t["sector"]] += PUMP_RATE
                    set_status("On site — pumping", f"{round(self.depth[t['sector']])} cm water measured by crew; unblocking nullah inlets")
                else:
                    set_status("On site", "Rescue team assisting")
            elif t["status"].startswith("On site"):
                if t["kind"] == "drain" and self.depth[t["sector"]] < 4:
                    self.pumping[t["sector"]] = max(0.0, self.pumping[t["sector"]] - PUMP_RATE)
                    self.drain_fixed[t["sector"]] = 1.8  # cleared inlets restore normal drainage
                    set_status("Resolved", "Inlets cleared, water drained, road declared safe")
                    self.reopen_roads(t["incidentId"])
                elif t["kind"] == "rescue" and age >= 2 + t["etaMin"] + 6:
                    set_status("Resolved", "Motorcyclist assisted, no further casualties")

    def reopen_roads(self, incident_id: str) -> None:
        inc = next((i for i in self.incidents if i["id"] == incident_id), None)
        reroute = next((x for x in inc["plan"]["actions"] if x["type"] == "reroute"), None) if inc and inc["plan"] else None
        if reroute:
            self.closed.difference_update(reroute["links"])
            self.log("traffic", f"Roads around {inc['sector']} reopened after crew confirmation.", inc["sector"])
            self.trail(inc, "reopen", "Crew confirmed road safe — closures lifted, normal routing restored.")

    def resolve(self, inc: dict) -> None:
        inc["status"] = "resolved"
        inc["resolvedAt"] = self.sim_min
        inc["resolvedClock"] = self.clock()
        self.informed.discard(inc["sector"])
        inc["execution"]["after"] = {
            "sectorTTI": self.traffic_now["sectors"][inc["sector"]]["tti"],
            "metrics": dict(self.traffic_now["metrics"]),
            "score": inc["assessment"]["final"]["score"],
        }
        self.trail(inc, "resolve", f"Resolved after {inc['resolvedAt'] - inc['openedAt']} min. "
                                   f"Score {inc['execution']['before']['score']} → {inc['assessment']['final']['score']}.")
        self.log("incident", f"{inc['id']} resolved.", inc["sector"])
        n = self.world["sectors"][inc["sector"]]["residents"]
        self.seq["bc"] += 1
        all_clear = {
            "id": f"BC-{self.seq['bc']}", "incidentId": inc["id"], "sectors": [inc["sector"]], "clock": self.clock(),
            "simMin": self.sim_min, "kind": "all_clear",
            "message": {"en": f"[Nikaasi ALL CLEAR] Water cleared in {inc['sector']}. Roads reopened. Drive carefully.",
                        "ur": f"[Nikaasi] {inc['sector']} mein pani nikal gaya hai, sarkein khul gayi hain."},
            "recipients": n, "delivered": n, "failed": 0,
        }
        self.broadcasts.append(all_clear)
        self.store.save_broadcast(all_clear)

    def send_broadcast(self, inc: dict, action: dict) -> None:
        recipients = action["recipients"]
        failed = round(recipients * (0.02 + self.rng.random() * 0.03))
        self.seq["bc"] += 1
        bc = {
            "id": f"BC-{self.seq['bc']}", "incidentId": inc["id"], "sectors": action["sectors"], "clock": self.clock(),
            "simMin": self.sim_min, "kind": "warning", "severity": inc["severity"], "message": action["message"],
            "recipients": recipients, "delivered": recipients - failed, "failed": failed,
            "channels": ["SMS", "App push", "WhatsApp (mock)"],
        }
        self.broadcasts.append(bc)
        self.store.save_broadcast(bc)
        self.informed.update(action["sectors"])
        self.log("alert", f"Alert {bc['id']} sent to {recipients:,} residents ({bc['delivered']:,} delivered, {failed} failed).", inc["sector"])

    def trail(self, inc: dict, kind: str, text: str, a: dict | None = None) -> None:
        entry = {"clock": self.clock(), "simMin": self.sim_min, "kind": kind, "text": text}
        if a:
            f = a["final"]
            entry["snapshot"] = {"score": f["score"], "severity": f["severity"], "confidence": f["confidence"],
                                 "breakdown": a["breakdown"], "engine": f["engine"]}
        inc["trail"].append(entry)
        self.store.save_incident(inc)
        self.store.save_trail(inc, entry)

    # ------------------------------------------------------------ authority controls
    def set_pmd_alert(self, sector: str, level: str, by: str = "duty officer", hours: float = PMD_ALERT_HOURS) -> None:
        """Record an official PMD alert (PMD has no public API, so an officer enters it)."""
        if sector not in self.world["sectors"]:
            raise HttpError(400, "Unknown sector")
        if level in ("clear", "none"):
            if self.pmd_alerts.pop(sector, None):
                self.log("weather", f"PMD alert for {sector} cleared by {by}.", sector)
        elif level in ("yellow", "orange", "red"):
            until = datetime.now(timezone.utc) + timedelta(hours=hours)
            self.pmd_alerts[sector] = {"level": level, "by": by, "until": until}
            self.log("weather", f"PMD {level.upper()} alert for {sector} entered by {by} (valid until {until.astimezone(PKT):%H:%M}).", sector)
        else:
            raise HttpError(400, "Bad alert level")
        self.update_weather()
        self.fuse_all()
        self.manage_incidents()
        self.emit()

    def set_weather_override(self, sector: str, alert: str, rain_mm_hr, by: str = "duty officer") -> None:
        if sector not in self.world["sectors"]:
            raise HttpError(400, "Unknown sector")
        if self.mode == "live":  # real weather is never overridden; the officer records the PMD alert instead
            self.set_pmd_alert(sector, alert, by)
            return
        if alert == "clear":
            self.manual_weather.pop(sector, None)
            self.log("weather", f"Manual weather override cleared for {sector}.", sector)
        else:
            if alert not in ("none", "yellow", "orange", "red"):
                raise HttpError(400, "Bad alert level")
            try:
                rain = clamp(float(rain_mm_hr or 0), 0, 150)
            except (TypeError, ValueError):
                rain = 0.0
            self.manual_weather[sector] = {"alert": alert, "rainMmHr": rain}
            self.log("weather", f"Manual weather override: {sector} {alert.upper()} {rain:g} mm/h.", sector)
        self.update_weather()
        self.fuse_all()
        self.manage_incidents()
        self.emit()

    # ------------------------------------------------------------ views
    def clock(self, minute: int | None = None) -> str:
        if getattr(self, "mode", "demo") == "live" and minute is None:
            return datetime.now(PKT).strftime("%H:%M")
        m = (START_CLOCK_MIN + (self.sim_min if minute is None else minute)) % (24 * 60)
        return f"{m // 60:02d}:{m % 60:02d}"

    def log(self, kind: str, text: str, sector: str | None = None, persist: bool = True) -> None:
        e = {"clock": self.clock(), "simMin": self.sim_min, "type": kind, "text": text, "sector": sector}
        self.events.append(e)
        if persist:
            self.store.save_event(e)
        del self.events[:-300]

    def public_assessment(self, s: str) -> dict:
        a = self.assessments[s]
        f = a["final"]
        return {"sector": s, "score": f["score"], "severity": f["severity"], "confidence": f["confidence"],
                "confidencePct": a["confidencePct"], "explanation": f["explanation"], "engine": f["engine"], "ai": f.get("ai"),
                "rules": {"score": a["score"], "severity": a["severity"], "explanation": a["explanation"]},
                "breakdown": a["breakdown"], "signals": a["signals"]}

    def weather_source(self) -> dict:
        if self.mode != "live":
            return {"mode": "demo", "provider": "Scripted demo (PMD mock)"}
        f = self.live["fetchedAt"]
        return {
            "mode": "live", "provider": weather_live.PROVIDER, "attribution": weather_live.ATTRIBUTION,
            "ok": self.live["ok"], "error": "unavailable" if self.live["error"] else None,
            "fetchedAt": f.astimezone(PKT).strftime("%H:%M") if f else None,
            "thresholdsMmHr": weather_live.thresholds(),
            "pmdAlerts": {k: {"level": v["level"], "by": v["by"], "until": v["until"].astimezone(PKT).strftime("%H:%M")}
                          for k, v in self.pmd_alerts.items()},
        }

    def ai_status(self) -> dict:
        return {"enabled": gemini.enabled(), "model": gemini.model() if gemini.enabled() else None}

    def state(self, authority: bool = True) -> dict:
        base = {
            "clock": self.clock(), "simMin": self.sim_min, "running": self.running, "speed": self.speed,
            "scenario": self.scenario["name"] if self.scenario else None,
            "ai": self.ai_status(),
            "mode": self.mode,
            "weatherSource": self.weather_source(),
            "db": {"enabled": self.store.enabled, "runId": str(self.store.run_id) if self.store.run_id else None},
            "settings": self.settings,
            "sectors": {s: self.public_assessment(s) for s in self.world["sectors"]},
            "weather": self.weather,
            "traffic": {
                "sectors": {k: {**v, "baseline": self.baseline["sectors"][k]["tti"]} for k, v in self.traffic_now["sectors"].items()},
                "links": self.traffic_now["links"], "metrics": self.traffic_now["metrics"], "baselineMetrics": self.baseline["metrics"],
            },
            "closed": sorted(self.closed),
            "broadcasts": self.broadcasts[-20:],
        }
        if not authority:
            return base
        incidents = []
        for i in self.incidents:
            d = dict(i)
            d["assessment"] = self.public_assessment(i["sector"]) if i.get("assessment") else None
            incidents.append(d)
        return {
            **base,
            "shadow": ({k: self.shadow[k] for k in ("links", "sectors", "metrics")} if self.shadow else None),
            "truth": {"depth": {k: round(v, 1) for k, v in self.depth.items()},
                      "shadowDepth": {k: round(v, 1) for k, v in self.shadow_depth.items()}},
            "incidents": incidents,
            "tickets": self.tickets,
            "trafficHistory": {k: [x["tti"] for x in h[-30:]] for k, h in self.traffic_history.items()},
            "reports": [{k: v for k, v in r.items() if k != "position"} for r in self.reports[-60:]],
            "events": self.events[-80:],
        }
