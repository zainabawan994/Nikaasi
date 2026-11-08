"""
Response planner: turns a fused assessment into concrete, simulatable actions.
It only uses what the system can observe (reports + feeds), never the hidden
ground-truth water depth.
"""
from __future__ import annotations

import time

from .config import neighbours
from .fusion import RANK
from .traffic import simulate, travel_time


def estimate_depth(assessment: dict) -> int:
    c = assessment["signals"]["citizen"]
    t = assessment["signals"]["traffic"]
    d = 8
    if "road_blocked" in c["hazards"]:
        d = 25
    if "vehicle_stranded" in c["hazards"] or "people_at_risk" in c["hazards"]:
        d = 38
    if t["spike"] >= 3:
        d = max(d, 35)
    elif t["spike"] >= 1.8:
        d = max(d, 22)
    return d


def affected_links(world: dict, sector: str, observed: dict, assessment: dict) -> list[str]:
    ids: list[str] = []
    bordering = [l for l in world["links"] if sector in l["sectors"]]
    for l in bordering:
        o = observed.get(l["id"])
        if l.get("underpass") and assessment["signals"]["citizen"]["mentionsUnderpass"]:
            ids.append(l["id"])
        elif o and o["tti"] >= 2.5 and l["kind"] == "arterial":
            ids.append(l["id"])
    if not ids:
        arterials = sorted((l for l in bordering if l["kind"] == "arterial"),
                           key=lambda l: observed.get(l["id"], {}).get("tti", 1), reverse=True)
        if arterials and observed.get(arterials[0]["id"], {}).get("tti", 1) > 1.5:
            ids.append(arterials[0]["id"])
    return ids


def choose_depot(world: dict, kind: str, sector: str, depths_guess: dict) -> dict:
    options = []
    for d in world["depots"]:
        if d["kind"] != kind:
            continue
        minutes, path = travel_time(world, depths_guess, set(), d["node"], sector)
        options.append({"depot": d, "minutes": minutes, "path": path})
    return min(options, key=lambda o: o["minutes"])


def compose_alert(sector: str, sev: str, closed_names: str | None, c: dict) -> dict:
    up_en = " & underpasses" if c["mentionsUnderpass"] else ""
    up_ur = " aur underpass" if c["mentionsUnderpass"] else ""
    en = (f"[Nikaasi {sev.upper()}] Flooding in {sector}."
          + (f" {closed_names} closed — use alternate routes." if closed_names else "")
          + f" Avoid low roads{up_en}, move vehicles to higher ground. Emergency: 1122.")
    ur = (f"[Nikaasi] {sector} mein pani bhar gaya hai."
          + (f" {closed_names} band hai, doosra rasta lein." if closed_names else "")
          + f" Neechi sarkon{up_ur} se door rahein. Emergency: 1122.")
    return {"en": en, "ur": ur}


def plan(world: dict, assessment: dict, observed_links: dict, existing: dict | None) -> dict | None:
    sector = assessment["sector"]
    sev = assessment["severity"]
    rank = RANK[sev]
    if rank < 1:
        return None
    c = assessment["signals"]["citizen"]
    depth_guess = estimate_depth(assessment)
    depths_guess = {sector: depth_guess}
    priority = "P1" if rank >= 3 else "P2" if rank == 2 else "P3"
    by_id = world["links_by_id"]
    actions: list[dict] = []

    # 1. traffic
    close = affected_links(world, sector, observed_links, assessment) if rank >= 2 else []
    closed_names = ", ".join(dict.fromkeys(by_id[i]["name"] for i in close)) if close else None
    if close:
        actions.append({
            "id": "reroute", "type": "reroute", "icon": "route",
            "title": f"Close {len(close)} flooded segment{'s' if len(close) > 1 else ''} & reroute traffic",
            "detail": f"Close {closed_names}. ITP Patrol 12 to divert at adjacent junctions; navigation advisories push alternate routes.",
            "links": close,
        })
    else:
        actions.append({
            "id": "advisory", "type": "traffic_advisory", "icon": "route",
            "title": "Traffic advisory to ITP",
            "detail": f"Ask Islamabad Traffic Police to monitor {sector} approaches; no closure yet (severity {sev}).",
            "links": [],
        })

    # 2. drain team
    drain = choose_depot(world, "drain", sector, depths_guess)
    actions.append({
        "id": "dispatch_drain", "type": "dispatch", "icon": "truck", "priority": priority,
        "title": f"Dispatch {drain['depot']['team']}" + (" with suction pump" if rank >= 2 else " (inspection)"),
        "detail": (f"{priority} ticket to {drain['depot']['name']}. ETA ≈ {round(drain['minutes'])} min via "
                   f"{len(drain['path'])} road segments. Task: clear blocked nullah inlets in {sector}"
                   + (" and pump out the underpass" if c["mentionsUnderpass"] else "") + "."),
        "team": drain["depot"]["team"], "depot": drain["depot"]["id"], "etaMin": round(drain["minutes"]), "route": drain["path"],
    })

    # 3. rescue
    if c["peopleAtRisk"] or (rank >= 3 and "vehicle_stranded" in c["hazards"]):
        r = choose_depot(world, "rescue", sector, depths_guess)
        actions.append({
            "id": "dispatch_rescue", "type": "dispatch", "icon": "medic", "priority": "P1",
            "title": f"Dispatch {r['depot']['team']}",
            "detail": (f"Citizen reports indicate {'a person may be injured/at risk' if c['peopleAtRisk'] else 'stranded vehicles'}. "
                       f"ETA ≈ {round(r['minutes'])} min."),
            "team": r["depot"]["team"], "depot": r["depot"]["id"], "etaMin": round(r["minutes"]), "route": r["path"],
        })

    # 4. residents
    alert_sectors = [sector, *neighbours(world, sector)] if rank >= 2 else [sector]
    recipients = sum(world["sectors"][s]["residents"] for s in alert_sectors)
    actions.append({
        "id": "alert", "type": "alert", "icon": "bell",
        "title": f"Alert {recipients:,} residents in {len(alert_sectors)} sector{'s' if len(alert_sectors) > 1 else ''}",
        "detail": f"SMS + app push to opted-in residents of {', '.join(alert_sectors)}.",
        "sectors": alert_sectors, "recipients": recipients,
        "message": compose_alert(sector, sev, closed_names, c),
    })

    # predicted impact, based on *estimated* depth, not ground truth
    informed = {sector} if rank >= 2 else set()
    no_action = simulate(world, depths_guess)
    with_action = simulate(world, depths_guess, set(close), informed)
    prediction = {
        "depthGuessCm": depth_guess,
        "sectorTTI": {"before": no_action["sectors"][sector]["tti"], "after": with_action["sectors"][sector]["tti"]},
        "avgTripMin": {"before": no_action["metrics"]["avgTripMin"], "after": with_action["metrics"]["avgTripMin"]},
        "exposurePerHr": {"before": no_action["metrics"]["exposurePerHr"], "after": with_action["metrics"]["exposurePerHr"]},
    }
    version = existing["version"] + (1 if existing["severity"] != sev else 0) if existing else 1
    return {"severity": sev, "priority": priority, "actions": actions, "prediction": prediction,
            "version": version, "createdAt": int(time.time() * 1000)}
