"""
Mesoscopic traffic model.
  - Directed links with BPR volume-delay: t = t0 * (1 + 0.15 (v/c)^4)
  - Incremental assignment (4 x 25 %) of an OD demand matrix over Dijkstra
  - Floodwater reduces *actual* capacity; drivers who have not been warned or
    rerouted keep routing on their usual (unflooded) perception.
"""
from __future__ import annotations

import heapq
import math

MAX_TTI = 15        # gridlock cap on travel-time index
IMPASSABLE_CM = 35  # cars stall above this depth
EXPOSURE_CM = 30    # vehicles entering water deeper than this risk stalling / floating
STEPS = 4


def build_demand(world: dict) -> list[dict]:
    sec = list(world["sectors"].values())
    od = []
    for a in sec:
        for b in sec:
            if a["id"] == b["id"]:
                continue
            d = abs(a["i"] - b["i"]) + abs(a["j"] - b["j"])
            od.append({"o": a["markaz"], "d": b["markaz"], "flow": 75 / (1 + d)})
    through = [
        ("n0_2", "n4_2", 2400), ("n4_2", "n0_2", 2600),  # Srinagar Hwy
        ("n0_1", "n4_1", 500), ("n4_1", "n0_1", 500),    # Nazimuddin Rd
        ("n3_0", "n3_3", 800), ("n3_3", "n3_0", 800),    # 9th Ave
        ("n1_0", "n1_3", 350), ("n1_3", "n1_0", 350),    # 11th Ave
    ]
    od += [{"o": o, "d": d, "flow": f} for o, d, f in through]
    return od


def _directed(world: dict) -> list[dict]:
    out = []
    for l in world["links"]:
        out.append({"key": f"{l['id']}>", "link": l, "from": l["a"], "to": l["b"]})
        out.append({"key": f"{l['id']}<", "link": l, "from": l["b"], "to": l["a"]})
    return out


def link_flood(link: dict, depths: dict) -> tuple[float, float]:
    """Return (effective depth cm, capacity factor) for a road."""
    d = max((depths.get(s, 0) for s in link["sectors"]), default=0)
    if link.get("underpass"):
        d *= 2.2  # underpasses pool water
    if link["kind"] == "local":
        d *= 1.2
    factor = 1.0 if d < 5 else max(0.06, 1 - d / 45)
    return d, factor


def bpr(t0: float, v: float, c: float) -> float:
    x = v / c if c > 0 else 99
    return t0 * min(MAX_TTI, 1 + 0.15 * x ** 4)


def dijkstra(nodes: dict, adj: dict, src: str) -> tuple[dict, dict]:
    dist = {n: math.inf for n in nodes}
    prev: dict[str, dict] = {}
    dist[src] = 0.0
    heap = [(0.0, src)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist[u]:
            continue
        for e in adj.get(u, ()):
            c = e["cost"]
            if not math.isfinite(c):
                continue
            nd = d + c
            if nd < dist[e["to"]]:
                dist[e["to"]] = nd
                prev[e["to"]] = e
                heapq.heappush(heap, (nd, e["to"]))
    return dist, prev


def simulate(world: dict, depths: dict, closed: set | None = None, informed: set | None = None) -> dict:
    """
    depths:   {sector: cm} actual water depth
    closed:   link ids closed by traffic police
    informed: sectors whose flooding drivers know about (alerts / diversions)
    """
    closed = closed or set()
    informed = informed or set()
    dl = _directed(world)
    adj: dict[str, list] = {}
    for e in dl:
        l = e["link"]
        e["t0"] = l["km"] / l["speed"] * 60
        e["depth"], factor = link_flood(l, depths)
        e["cap_actual"] = l["cap"] * factor
        knows = any(s in informed for s in l["sectors"])
        e["cap_perceived"] = e["cap_actual"] if knows else l["cap"]
        e["closed"] = l["id"] in closed
        e["flow"] = 0.0
        adj.setdefault(e["from"], []).append(e)

    # residents who were alerted about a flooded sector largely stay put
    suppressed = {world["sectors"][s]["markaz"] for s in informed if depths.get(s, 0) >= 15}
    demand = [dict(q, flow=q["flow"] * 0.3) if (q["o"] in suppressed or q["d"] in suppressed) else q
              for q in build_demand(world)]
    by_origin: dict[str, list] = {}
    for q in demand:
        by_origin.setdefault(q["o"], []).append(q)

    unroutable = 0.0
    for _ in range(STEPS):
        for e in dl:
            if e["closed"]:
                e["cost"] = math.inf
            else:
                e["cost"] = bpr(e["t0"], e["flow"], e["cap_perceived"])
                if e["cap_perceived"] < e["link"]["cap"] and e["depth"] >= IMPASSABLE_CM:
                    e["cost"] = e["t0"] * 40  # known-impassable
        for o, qs in by_origin.items():
            _, prev = dijkstra(world["nodes"], adj, o)
            for q in qs:
                n, path = q["d"], []
                while n != o:
                    e = prev.get(n)
                    if e is None:
                        path = []
                        break
                    path.append(e)
                    n = e["from"]
                if not path:
                    unroutable += q["flow"] / STEPS
                    continue
                for e in path:
                    e["flow"] += q["flow"] / STEPS

    veh_min = exposure = stalled = 0.0
    total_trips = sum(q["flow"] for q in demand)
    for e in dl:
        e["t_actual"] = 0 if e["closed"] else bpr(e["t0"], e["flow"], e["cap_actual"])
        if not e["closed"] and e["depth"] >= IMPASSABLE_CM and e["flow"] > 0:
            e["t_actual"] = e["t0"] * MAX_TTI
            stalled += e["flow"] * 0.04
        veh_min += e["flow"] * e["t_actual"]
        if e["depth"] >= EXPOSURE_CM:
            exposure += e["flow"]

    links = {l["id"]: {"id": l["id"], "flow": 0.0, "vc": 0.0, "tti": 1.0, "depth": 0.0, "closed": l["id"] in closed}
             for l in world["links"]}
    for e in dl:
        L = links[e["link"]["id"]]
        L["flow"] += e["flow"]
        L["vc"] = max(L["vc"], e["flow"] / e["cap_actual"] if e["cap_actual"] > 0 else 0)
        L["tti"] = max(L["tti"], 1 if e["closed"] else e["t_actual"] / e["t0"])
        L["depth"] = max(L["depth"], e["depth"])
    for L in links.values():
        L["flow"] = round(L["flow"])
        L["vc"] = round(L["vc"], 2)
        L["tti"] = round(L["tti"], 2)
        L["depth"] = round(L["depth"], 1)

    sectors = {}
    for s in world["sectors"].values():
        ls = [l for l in world["links"] if s["id"] in l["sectors"] and l["id"] not in closed]
        # flow-weighted so a stalled but emptied street counts less than a jammed highway
        wsum = sum(links[l["id"]]["flow"] + 100 for l in ls)
        tti = sum(links[l["id"]]["tti"] * (links[l["id"]]["flow"] + 100) for l in ls) / wsum if ls else 1
        vc = sum(links[l["id"]]["vc"] for l in ls) / len(ls) if ls else 0
        sectors[s["id"]] = {"tti": round(tti, 2), "vc": round(vc, 2)}

    return {
        "links": links,
        "sectors": sectors,
        "metrics": {
            "avgTripMin": round(veh_min / total_trips, 1),
            "vehHours": round(veh_min / 60),
            "exposurePerHr": round(exposure),
            "stalledVehicles": round(stalled),
            "unroutable": round(unroutable),
            "closedLinks": len(closed),
        },
    }


def travel_time(world: dict, depths: dict, closed: set, from_node: str, sector_id: str) -> tuple[float, list[str]]:
    """Emergency-vehicle travel time (min) and route (link ids) to a sector's markaz."""
    adj: dict[str, list] = {}
    for e in _directed(world):
        d, _ = link_flood(e["link"], depths)
        t0 = e["link"]["km"] / e["link"]["speed"] * 60
        # emergency vehicles may use closed roads but are slowed by water
        e["cost"] = math.inf if d >= IMPASSABLE_CM * 1.5 else t0 * (1 + d / 15) * (1.2 if e["link"]["id"] in closed else 1)
        adj.setdefault(e["from"], []).append(e)
    dist, prev = dijkstra(world["nodes"], adj, from_node)
    target = world["sectors"][sector_id]["markaz"]
    path, n = [], target
    while n != from_node and n in prev:
        path.insert(0, prev[n]["link"]["id"])
        n = prev[n]["from"]
    return dist[target], path
