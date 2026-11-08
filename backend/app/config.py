"""
Static world model: a schematic (not to scale) map of 12 Islamabad sectors
(F/G/H rows x 11/10/9/8 columns) with an arterial road grid, a local "markaz"
node inside every sector, response depots and a mock resident registry.
"""
from __future__ import annotations

import math

COLS = ["11", "10", "9", "8"]  # west -> east
ROWS = ["F", "G", "H"]         # north -> south
ORIGIN_X, ORIGIN_Y, CELL_W, CELL_H = 60, 50, 170, 150

# Approximate real-world centres (only used to snap a phone GPS fix to a sector)
SECTOR_LATLNG = {
    "F-11": [33.6845, 72.9880], "F-10": [33.6950, 73.0120], "F-9": [33.7020, 73.0270], "F-8": [33.7110, 73.0400],
    "G-11": [33.6680, 72.9980], "G-10": [33.6760, 73.0150], "G-9": [33.6850, 73.0310], "G-8": [33.6960, 73.0480],
    "H-11": [33.6500, 73.0080], "H-10": [33.6600, 73.0250], "H-9": [33.6700, 73.0480], "H-8": [33.6820, 73.0640],
}

# Mock runoff / drainage. G-10 has a half-blocked nullah and a low underpass:
# this is hidden "ground truth" the AI never sees directly.
SECTOR_HYDRO = {
    "G-10": {"runoff": 1.0, "drain": 0.25},
    "G-11": {"runoff": 0.35, "drain": 0.9},
    "F-10": {"runoff": 0.4, "drain": 0.8},
}
DEFAULT_HYDRO = {"runoff": 0.3, "drain": 0.9}

RESIDENTS = {
    "F-11": 860, "F-10": 1420, "F-9": 120, "F-8": 980,
    "G-11": 1310, "G-10": 1745, "G-9": 1590, "G-8": 1180,
    "H-11": 540, "H-10": 690, "H-9": 410, "H-8": 300,
}

H_ROADS = ["Margalla Rd", "Nazimuddin Rd", "Srinagar Hwy", "IJP Rd"]
V_ROADS = ["Golra Rd", "11th Ave", "Ibn-e-Sina Rd", "9th Ave", "7th Ave"]
H_CLASS = [(1600, 50), (1800, 50), (4200, 80), (2200, 60)]  # (cap veh/h/dir, km/h)
V_CLASS = [(1400, 50), (1500, 50), (1800, 50), (2400, 60), (1800, 50)]


def build_world() -> dict:
    nodes: dict[str, dict] = {}
    sectors: dict[str, dict] = {}
    links: list[dict] = []

    for i in range(len(COLS) + 1):
        for j in range(len(ROWS) + 1):
            nid = f"n{i}_{j}"
            nodes[nid] = {"id": nid, "x": ORIGIN_X + i * CELL_W, "y": ORIGIN_Y + j * CELL_H, "kind": "junction"}

    for j, r in enumerate(ROWS):
        for i, c in enumerate(COLS):
            sid = f"{r}-{c}"
            cx = ORIGIN_X + i * CELL_W + CELL_W / 2
            cy = ORIGIN_Y + j * CELL_H + CELL_H / 2
            sectors[sid] = {
                "id": sid, "row": r, "col": c, "i": i, "j": j,
                "x": ORIGIN_X + i * CELL_W, "y": ORIGIN_Y + j * CELL_H, "w": CELL_W, "h": CELL_H,
                "cx": cx, "cy": cy,
                "latlng": SECTOR_LATLNG[sid],
                "hydro": SECTOR_HYDRO.get(sid, DEFAULT_HYDRO),
                "residents": RESIDENTS.get(sid, 500),
                "markaz": f"m_{sid}",
            }
            nodes[f"m_{sid}"] = {"id": f"m_{sid}", "x": cx, "y": cy, "kind": "markaz", "sector": sid}

    def len_km(a: str, b: str) -> float:
        return math.hypot(nodes[a]["x"] - nodes[b]["x"], nodes[a]["y"] - nodes[b]["y"]) / CELL_W * 1.9

    for j in range(len(ROWS) + 1):
        for i in range(len(COLS)):
            a, b = f"n{i}_{j}", f"n{i + 1}_{j}"
            cap, speed = H_CLASS[j]
            links.append({"id": f"h{i}_{j}", "a": a, "b": b, "name": H_ROADS[j], "cap": cap, "speed": speed, "km": len_km(a, b), "kind": "arterial"})
    for i in range(len(COLS) + 1):
        for j in range(len(ROWS)):
            a, b = f"n{i}_{j}", f"n{i}_{j + 1}"
            cap, speed = V_CLASS[i]
            links.append({"id": f"v{i}_{j}", "a": a, "b": b, "name": V_ROADS[i], "cap": cap, "speed": speed, "km": len_km(a, b), "kind": "arterial"})
    for s in sectors.values():
        corners = [(s["i"], s["j"]), (s["i"] + 1, s["j"]), (s["i"], s["j"] + 1), (s["i"] + 1, s["j"] + 1)]
        for k, (i, j) in enumerate(corners):
            a, b = s["markaz"], f"n{i}_{j}"
            links.append({"id": f"l_{s['id']}_{k}", "a": a, "b": b, "name": f"{s['id']} local street", "cap": 700, "speed": 30,
                          "km": len_km(a, b), "kind": "local", "sector": s["id"]})

    underpass = next(l for l in links if l["id"] == "h1_2")
    underpass["name"] = "Srinagar Hwy — G-10 Underpass"
    underpass["underpass"] = True

    for l in links:
        if l.get("sector"):
            l["sectors"] = [l["sector"]]
        else:
            l["sectors"] = [s["id"] for s in sectors.values()
                            if _on_sector_edge(nodes[l["a"]], s) and _on_sector_edge(nodes[l["b"]], s)]

    depots = [
        {"id": "DEP-H9", "name": "CDA Drain Depot H-9", "node": "n3_3", "team": "CDA Drain Team 3", "kind": "drain"},
        {"id": "DEP-F8", "name": "CDA Drain Depot F-8", "node": "n4_0", "team": "CDA Drain Team 1", "kind": "drain"},
        {"id": "DEP-1122", "name": "Rescue 1122 Station G-9", "node": "n2_1", "team": "Rescue 1122 Unit 7", "kind": "rescue"},
        {"id": "DEP-ITP", "name": "ITP Sector Office G-11", "node": "n0_2", "team": "ITP Patrol 12", "kind": "police"},
    ]
    return {
        "nodes": nodes, "sectors": sectors, "links": links, "depots": depots,
        "links_by_id": {l["id"]: l for l in links},
        "size": {"w": ORIGIN_X * 2 + CELL_W * len(COLS), "h": ORIGIN_Y * 2 + CELL_H * len(ROWS)},
    }


def _on_sector_edge(n: dict, s: dict) -> bool:
    eps = 0.1
    in_x = s["x"] - eps <= n["x"] <= s["x"] + s["w"] + eps
    in_y = s["y"] - eps <= n["y"] <= s["y"] + s["h"] + eps
    on_border = (abs(n["x"] - s["x"]) < eps or abs(n["x"] - s["x"] - s["w"]) < eps
                 or abs(n["y"] - s["y"]) < eps or abs(n["y"] - s["y"] - s["h"]) < eps)
    return in_x and in_y and on_border


def sector_from_latlng(world: dict, lat: float, lng: float) -> str | None:
    best, best_d = None, math.inf
    for s in world["sectors"].values():
        d = math.hypot(s["latlng"][0] - lat, (s["latlng"][1] - lng) * math.cos(math.radians(lat)))
        if d < best_d:
            best, best_d = s["id"], d
    return best if best_d < 0.03 else None


def sector_from_xy(world: dict, x: float, y: float) -> str | None:
    for s in world["sectors"].values():
        if s["x"] <= x <= s["x"] + s["w"] and s["y"] <= y <= s["y"] + s["h"]:
            return s["id"]
    return None


def neighbours(world: dict, sector: str) -> list[str]:
    s = world["sectors"][sector]
    return [q["id"] for q in world["sectors"].values() if abs(q["i"] - s["i"]) + abs(q["j"] - s["j"]) == 1]
