"""Long-running server: memory and payload stay bounded; privacy helpers work."""
import asyncio
import base64
import json

from app import engine as engine_mod
from app import gemini
from app.engine import Engine
from app.scenario import G10_MONSOON
from app.security import strip_metadata


def run_storm(e: Engine) -> None:
    e.scenario = {"name": "t", "startedAt": e.sim_min, "script": [dict(s, done=False) for s in G10_MONSOON]}
    for _ in range(14):
        e.tick()
    for inc in [i for i in e.incidents if i["status"] != "resolved" and i["planPending"]]:
        e.execute(inc["id"], "t")
    for _ in range(70):
        e.tick()


def test_staff_payload_and_memory_stay_bounded(monkeypatch):
    monkeypatch.setattr(engine_mod, "KEEP_RESOLVED_INCIDENTS", 6)
    monkeypatch.setattr(engine_mod, "SEND_RESOLVED_INCIDENTS", 3)
    monkeypatch.setattr(engine_mod, "KEEP_REPORTS", 10)
    e = Engine()
    sizes = []
    for _ in range(6):
        run_storm(e)
        sizes.append(len(json.dumps(e.state())))
    resolved = [i for i in e.incidents if i["status"] == "resolved"]
    assert len(resolved) <= 6, "old resolved incidents are pruned from memory"
    assert len(e.reports) <= 10
    sent = e.state()["incidents"]
    assert len([i for i in sent if i["status"] == "resolved"]) <= 3
    assert max(sizes[2:]) <= sizes[1] * 1.1, f"payload must stop growing, got {sizes}"
    # only the newest resolved incidents keep heavy road snapshots
    detail = [i for i in sent if i["status"] == "resolved" and i.get("execution") and "links" in i["execution"]["before"]]
    assert len(detail) <= engine_mod.FULL_DETAIL_RESOLVED
    # tickets sent belong to incidents that are sent
    ids = {i["id"] for i in sent}
    assert all(t["incidentId"] in ids for t in e.state()["tickets"])


def test_no_response_model_stops_after_resolution():
    e = Engine()
    run_storm(e)
    assert all(i["status"] == "resolved" for i in e.incidents if i["execution"])
    e.tick()
    assert e.shadow is None, "the counterfactual model only runs while a response is active"


def test_photos_are_evicted_from_memory(monkeypatch):
    monkeypatch.setattr(engine_mod, "KEEP_PHOTOS", 2)
    e = Engine()
    jpg = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 32).decode()
    ids = [e.ingest_report(sector="G-9", reporter_id=f"u{i}", name=None, text="pani hai", ai=engine_mod.nlp.interpret("pani hai"), photo=jpg)["id"] for i in range(3)]
    assert list(e.photos) == ids[1:], "oldest photo evicted first"
    assert e.photo(ids[0]) is None, "without a database the evicted photo is gone"
    assert e.photo(ids[2]) == jpg


def test_metadata_is_stripped_from_png_and_webp():
    exif = b"GPS 33.67,73.01"
    png = (b"\x89PNG\r\n\x1a\n" + len(b"x" * 13).to_bytes(4, "big") + b"IHDR" + b"x" * 13 + b"\0\0\0\0"
           + len(exif).to_bytes(4, "big") + b"eXIf" + exif + b"\0\0\0\0" + (0).to_bytes(4, "big") + b"IEND" + b"\0\0\0\0")
    out = strip_metadata(png, "image/png")
    assert b"GPS" not in out and b"IHDR" in out and b"IEND" in out
    vp8 = b"VP8 " + (4).to_bytes(4, "little") + b"data"
    exf = b"EXIF" + len(exif).to_bytes(4, "little") + exif + b"\0"
    webp = b"RIFF" + (4 + len(vp8) + len(exf)).to_bytes(4, "little") + b"WEBP" + vp8 + exf
    out = strip_metadata(webp, "image/webp")
    assert b"GPS" not in out and out.endswith(vp8) and int.from_bytes(out[4:8], "little") == len(out) - 8


def test_citizen_report_uses_short_gemini_timeout(monkeypatch):
    seen = {}

    async def fake(parts, timeout=20.0, temperature=0.2, retries=1):
        seen.update(timeout=timeout, retries=retries)
        return {"floodEvidence": 0.9, "hazard": "none", "summary": "ok"}
    monkeypatch.setattr(gemini, "generate_json", fake)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    asyncio.run(gemini.interpret_report("pani bhar gaya hai", None))
    assert seen == {"timeout": 6.0, "retries": 0}
