"""
Google AI Studio (Gemini API) adapter.
Set GEMINI_API_KEY (https://aistudio.google.com/apikey) to enable. Every call has
a timeout and the engine falls back to the offline engines on any failure.
"""
from __future__ import annotations

import asyncio
import json
import os
import re

import httpx

API = "https://generativelanguage.googleapis.com/v1beta/models"
HAZARDS = ["people_at_risk", "vehicle_stranded", "road_blocked", "nuisance_flooding", "none"]


def enabled() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def model() -> str:
    return os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")


async def generate_json(parts: list[dict], timeout: float = 20.0, temperature: float = 0.2) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):
            res = await client.post(
                f"{API}/{model()}:generateContent",
                headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                json={"contents": [{"role": "user", "parts": parts}],
                      "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"}},
            )
            # Google returns 503 "high demand" / 429 in short bursts: retry once
            if res.status_code not in (429, 503) or attempt == 1:
                break
            await asyncio.sleep(2)
    if res.status_code != 200:
        raise RuntimeError(f"Gemini HTTP {res.status_code}: {res.text[:200]}")
    data = res.json()
    # thinking models may also return "thought" parts; keep only the answer
    text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"] if not p.get("thought"))
    text = re.sub(r"^```(json)?|```$", "", text.strip()).strip()
    return json.loads(text)


REPORT_PROMPT = """You triage citizen flood reports for Islamabad's municipal authority.
Reports may be English, Roman Urdu or Urdu (e.g. "pani bhar gaya hai", "gaari phans gayi hai", "road band hai").
If a photo is attached, use it to estimate water depth.
Return ONLY JSON:
{"floodEvidence": number 0-1 (how strongly this indicates real flooding now),
 "depth": "shallow (<10 cm)"|"moderate (10-30 cm)"|"deep (>30 cm)"|"unknown",
 "hazard": "people_at_risk"|"vehicle_stranded"|"road_blocked"|"nuisance_flooding"|"none",
 "peopleAtRisk": boolean,
 "mentionsUnderpass": boolean,
 "summary": "one short English sentence"}"""


async def interpret_report(text: str, photo_data_url: str | None) -> dict:
    parts: list[dict] = [{"text": f'{REPORT_PROMPT}\n\nReport text: """{str(text)[:1000]}"""'}]
    m = re.match(r"^data:(image/[a-z+]+);base64,(.+)$", photo_data_url or "", re.IGNORECASE | re.DOTALL)
    if m:
        parts.append({"inline_data": {"mime_type": m.group(1), "data": m.group(2)}})
    j = await generate_json(parts)
    return {
        "floodEvidence": _clamp01(j.get("floodEvidence")),
        "depth": j.get("depth") if isinstance(j.get("depth"), str) else "unknown",
        "hazard": j.get("hazard") if j.get("hazard") in HAZARDS else "none",
        "peopleAtRisk": bool(j.get("peopleAtRisk")),
        "mentionsUnderpass": bool(j.get("mentionsUnderpass")),
        "tags": [],
        "summary": str(j.get("summary", ""))[:200],
        "engine": model(),
    }


JUDGE_PROMPT = """You are the signal-fusion analyst for "Nikaasi", Islamabad's flood response system.
You receive three independent signals for one sector: citizen reports, a weather feed and a traffic feed,
plus a deterministic rule-engine score for reference. Judge how serious flooding really is.
Do not rely on citizen reports alone; weigh corroboration across signals; escalate if people are at risk.
Severity scale: Watch (<30), Moderate (30-54), High (55-74), Critical (75-100).
Return ONLY JSON:
{"score": integer 0-100, "severity": "Watch"|"Moderate"|"High"|"Critical",
 "confidence": "Low"|"Medium"|"High",
 "explanation": "one or two plain-language sentences for a duty officer, citing the concrete numbers (e.g. 'High confidence: 4 citizen reports + active red rainfall alert + traffic spike 2.1x in G-10 within the last 20 minutes')",
 "keyRisk": "short phrase"}"""


async def judge_incident(rules: dict, reports_sample: list[dict]) -> dict:
    payload = {
        "sector": rules["sector"],
        "citizen": {**rules["signals"]["citizen"], "sampleReports": reports_sample},
        "weather": rules["signals"]["weather"],
        "traffic": rules["signals"]["traffic"],
        "ruleEngine": {"score": rules["score"], "severity": rules["severity"], "confidence": rules["confidence"]},
    }
    j = await generate_json([{"text": f"{JUDGE_PROMPT}\n\nSignals:\n{json.dumps(payload, indent=1)}"}])
    try:
        score = round(float(j.get("score")))
    except (TypeError, ValueError):
        score = rules["score"]
    return {
        "score": max(0, min(100, score)),
        "confidence": j.get("confidence") if j.get("confidence") in ("Low", "Medium", "High") else rules["confidence"],
        "explanation": str(j.get("explanation", ""))[:400],
        "keyRisk": str(j.get("keyRisk", ""))[:80],
        "engine": model(),
    }


def _clamp01(x) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0
