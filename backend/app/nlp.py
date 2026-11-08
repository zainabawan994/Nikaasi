"""
Offline report interpreter (fallback when Gemini is not configured).
Understands English, Roman Urdu and a little Urdu script, and returns the same
shape as the Gemini adapter so fusion never cares which one ran.
"""
from __future__ import annotations

import re

_I = re.IGNORECASE
CUES: list[tuple[re.Pattern, float, str]] = [
    (re.compile(r"pani\s*(bhar|bhara|bhari|bhar\s*gaya|aa\s*gaya|khara|khada|jama)|پانی", _I), 0.85, "standing_water"),
    (re.compile(r"flood|flooded|waterlog|water\s*logg|submerged|inundat|sailab|سیلاب", _I), 0.9, "standing_water"),
    (re.compile(r"(knee|ghutno|ghutnay|kamar|waist)", _I), 0.95, "deep_water"),
    (re.compile(r"underpass|under\s*pass|pul\s*ke\s*neeche", _I), 0.6, "underpass"),
    (re.compile(r"(gaari|gari|gaadi|car|vehicle|bike|motor\s*cycle|rickshaw).{0,25}(phans|phas|stuck|band|bandh|stall|doob|dub)", _I), 0.9, "vehicle_stranded"),
    (re.compile(r"(phans|phas|stuck).{0,20}(gaari|gari|gaadi|car|vehicle)", _I), 0.9, "vehicle_stranded"),
    (re.compile(r"(gir\s*gaya|gir\s*gayi|fell|fallen|accident|injur|zakhmi|behosh|drown|doob\s*raha|help|bachao|madad)", _I), 0.7, "people_at_risk"),
    (re.compile(r"(road|sarak|sadak|rasta|street|gali).{0,15}(band|bandh|block|closed)", _I), 0.65, "road_blocked"),
    (re.compile(r"(nala|nullah|naala|drain|gutter|manhole).{0,25}(over|block|band|choke|ubal|bhar)", _I), 0.8, "drain_blocked"),
    (re.compile(r"(heavy|tez|bohat|shadeed).{0,10}(rain|barish|baarish)|cloudburst|موسلا", _I), 0.35, "heavy_rain"),
    (re.compile(r"(barish|baarish|rain)", _I), 0.2, "rain"),
]
NEGATIONS = re.compile(r"(no\s*water|pani\s*nahi|nahi\s*hai\s*pani|clear\s*now|saaf\s*hai|theek\s*hai|resolved|chala\s*gaya)", _I)
MINOR = re.compile(r"(thora|thoda|little|slight|halka|kam\s*pani)", _I)


def interpret(text: str, has_photo: bool = False) -> dict:
    t = str(text or "")
    tags: set[str] = set()
    evidence = 0.0
    for rx, w, tag in CUES:
        if rx.search(t):
            tags.add(tag)
            evidence = 1 - (1 - evidence) * (1 - w)
    if MINOR.search(t):
        evidence *= 0.6
        tags.add("minor")
    if NEGATIONS.search(t):
        evidence *= 0.2
        tags.add("negated")
    if has_photo:
        evidence = min(1.0, evidence + 0.08)

    if "deep_water" in tags or "vehicle_stranded" in tags:
        depth = "deep (>30 cm)"
    elif "standing_water" in tags and "minor" not in tags:
        depth = "moderate (10-30 cm)"
    elif "minor" in tags:
        depth = "shallow (<10 cm)"
    else:
        depth = "unknown"

    if "people_at_risk" in tags:
        hazard = "people_at_risk"
    elif "vehicle_stranded" in tags:
        hazard = "vehicle_stranded"
    elif "road_blocked" in tags or "deep_water" in tags:
        hazard = "road_blocked"
    elif evidence > 0.5:
        hazard = "nuisance_flooding"
    else:
        hazard = "none"

    return {
        "floodEvidence": round(evidence, 2),
        "depth": depth,
        "hazard": hazard,
        "peopleAtRisk": "people_at_risk" in tags and evidence > 0.4,
        "mentionsUnderpass": "underpass" in tags,
        "tags": sorted(tags),
        "summary": _summarize(tags, depth),
        "engine": "lexicon-v1",
    }


def _summarize(tags: set[str], depth: str) -> str:
    if "negated" in tags:
        return "Reporter indicates the water has cleared."
    parts = []
    if "standing_water" in tags or "deep_water" in tags:
        parts.append(f"standing water, {depth}")
    if "vehicle_stranded" in tags:
        parts.append("vehicle(s) stranded")
    if "people_at_risk" in tags:
        parts.append("person possibly hurt / needs help")
    if "road_blocked" in tags:
        parts.append("road impassable")
    if "underpass" in tags:
        parts.append("at underpass")
    if "drain_blocked" in tags:
        parts.append("drain blocked/overflowing")
    if not parts and ("rain" in tags or "heavy_rain" in tags):
        parts.append("rain reported, no flooding described")
    return "; ".join(parts) if parts else "No clear flood indicators in text."
