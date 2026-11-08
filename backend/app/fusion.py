"""
Signal-fusion severity rule (rules-v1). See docs/ARCHITECTURE.md.

  C (citizen, 0..1) = 1 - exp(-sum(w_i) / 2)
       w_i = floodEvidence x recency(30 min) x photo bonus, one vote per reporter
  W (weather, 0..1) = 0.6 x alertLevel + 0.4 x min(1, rain_mm_hr / 50)
  T (traffic, 0..1) = clamp((recency-weighted peak TTI in last 20 min / baseline - 1) / 1.5)

  score = 100 x (0.45 C + 0.25 W + 0.30 T)
        + 8 if two signals >= 0.4, +15 if all three   (corroboration)
        + 10 if a report says a person is at risk     (hazard, needs C >= 0.3)

  Watch < 30 <= Moderate < 55 <= High < 75 <= Critical
  Confidence: independent signals >= 0.3 -> 1 Low, 2 Medium, 3 High
"""
from __future__ import annotations

import math

CITIZEN_WINDOW = 30  # sim-minutes
TRAFFIC_WINDOW = 20
ALERT_SCORE = {"none": 0, "yellow": 0.35, "orange": 0.65, "red": 0.9}
WEIGHTS = {"citizen": 0.45, "weather": 0.25, "traffic": 0.30}
LEVELS = [("Watch", 0), ("Moderate", 30), ("High", 55), ("Critical", 75)]
RANK = {"Watch": 0, "Moderate": 1, "High": 2, "Critical": 3}


def severity_of(score: float) -> str:
    lvl = LEVELS[0][0]
    for name, lo in LEVELS:
        if score >= lo:
            lvl = name
    return lvl


def corroboration_bonus(*scores: float) -> int:
    strong = sum(1 for s in scores if s >= 0.4)
    return 15 if strong == 3 else 8 if strong == 2 else 0


def citizen_signal(reports: list[dict], now: int) -> dict:
    recent = [r for r in reports if 0 <= now - r["t"] <= CITIZEN_WINDOW]
    by_reporter: dict[str, float] = {}
    for r in recent:
        recency = 1 - (now - r["t"]) / CITIZEN_WINDOW * 0.6  # older reports keep 40 %
        w = r["ai"]["floodEvidence"] * recency * (1.15 if r["hasPhoto"] else 1)
        by_reporter[r["reporterId"]] = max(by_reporter.get(r["reporterId"], 0), min(1.0, w))
    total = sum(by_reporter.values())
    hazards = list(dict.fromkeys(r["ai"]["hazard"] for r in recent if r["ai"]["hazard"] != "none"))
    return {
        "score": round(1 - math.exp(-total / 2), 3),
        "count": len(recent),
        "uniqueReporters": len(by_reporter),
        "withPhoto": sum(1 for r in recent if r["hasPhoto"]),
        "peopleAtRisk": any(r["ai"]["peopleAtRisk"] for r in recent),
        "mentionsUnderpass": any(r["ai"]["mentionsUnderpass"] for r in recent),
        "hazards": hazards,
        "firstAt": min((r["t"] for r in recent), default=None),
    }


def weather_signal(w: dict | None) -> dict:
    if not w:
        return {"score": 0, "alert": "none", "rainMmHr": 0}
    a = ALERT_SCORE.get(w.get("alert"), 0)
    rain = w.get("rainMmHr") or 0
    score = 0.6 * a + 0.4 * min(1, rain / 50)
    return {"score": round(score, 3), "alert": w.get("alert", "none"), "rainMmHr": round(rain),
            "forecastMmHr": w.get("forecastMmHr"), "source": w.get("source")}


def traffic_signal(history: list[dict] | None, baseline: float | None, now: int) -> dict:
    win = [h for h in (history or []) if now - h["t"] <= TRAFFIC_WINDOW]
    if not win or not baseline:
        return {"score": 0, "spike": 1, "tti": 1, "trend": "flat"}
    # recency-weighted peak: a jam 20 min ago counts 20 %, one right now counts fully
    eff = [(h["t"], 1 + (h["tti"] - 1) * (1 - (now - h["t"]) / TRAFFIC_WINDOW * 0.8)) for h in win]
    peak_t, peak_v = max(eff, key=lambda x: x[1])
    spike = peak_v / baseline
    first, last = win[0]["tti"], win[-1]["tti"]
    trend = "rising" if last > first * 1.15 else "falling" if last < first * 0.87 else "flat"
    return {
        "score": round(max(0.0, min(1.0, (spike - 1) / 1.5)), 3),
        "spike": round(spike, 2), "tti": round(last, 2), "baseline": round(baseline, 2), "trend": trend,
        "minutesAgo": round(now - peak_t),
    }


def assess(sector: str, reports: list[dict], weather: dict | None, traffic_history: list[dict] | None,
           traffic_baseline: float | None, now: int) -> dict:
    C = citizen_signal(reports, now)
    W = weather_signal(weather)
    T = traffic_signal(traffic_history, traffic_baseline, now)

    base = 100 * (WEIGHTS["citizen"] * C["score"] + WEIGHTS["weather"] * W["score"] + WEIGHTS["traffic"] * T["score"])
    corroboration = corroboration_bonus(C["score"], W["score"], T["score"])
    hazard = 10 if C["peopleAtRisk"] and C["score"] >= 0.3 else 0
    score = round(min(100, base + corroboration + hazard))

    active = sum(1 for s in (C["score"], W["score"], T["score"]) if s >= 0.3)
    confidence = "High" if active >= 3 else "Medium" if active == 2 else "Low"
    confidence_pct = round(min(97, 30 + active * 18 + min(C["uniqueReporters"], 5) * 3 + (4 if T["trend"] == "rising" else 0)))

    factors = []
    if C["count"]:
        n = C["uniqueReporters"]
        factors.append(f"{n} citizen report{'' if n == 1 else 's'}" + (f" ({C['withPhoto']} with photo)" if C["withPhoto"] else ""))
    if W["alert"] != "none":
        factors.append(f"active {W['alert']} rainfall alert ({W['rainMmHr']} mm/h)")
    elif W["rainMmHr"] >= 10:
        factors.append(f"{W['rainMmHr']} mm/h rain, no alert")
    if T["spike"] >= 1.3:
        factors.append(f"traffic spike {T['spike']}× baseline" + (f" (peak {T['minutesAgo']} min ago)" if T.get("minutesAgo") is not None else ""))

    missing = []
    if not C["count"]:
        missing.append("no citizen reports")
    if W["alert"] == "none" and W["rainMmHr"] < 10:
        missing.append("no rainfall alert")
    if T["spike"] < 1.3:
        missing.append("traffic normal")

    hazard_note = (" A report indicates a person may be at risk." if C["peopleAtRisk"]
                   else " Vehicles reported stranded." if "vehicle_stranded" in C["hazards"] else "")
    explanation = (f"{confidence} confidence: {' + '.join(factors) if factors else 'no signals'} in {sector} "
                   f"within the last {TRAFFIC_WINDOW}–{CITIZEN_WINDOW} minutes."
                   + (f" Not corroborated by: {', '.join(missing)}." if missing else "") + hazard_note)

    return {
        "sector": sector, "score": score, "severity": severity_of(score), "confidence": confidence,
        "confidencePct": confidence_pct, "explanation": explanation,
        "breakdown": {
            "citizen": round(100 * WEIGHTS["citizen"] * C["score"], 1),
            "weather": round(100 * WEIGHTS["weather"] * W["score"], 1),
            "traffic": round(100 * WEIGHTS["traffic"] * T["score"], 1),
            "corroboration": corroboration, "hazard": hazard,
        },
        "signals": {"citizen": C, "weather": W, "traffic": T},
        "engine": "rules-v1",
    }
