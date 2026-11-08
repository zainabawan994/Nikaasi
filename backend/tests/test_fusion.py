from app.fusion import assess
from app.nlp import interpret


def rep(rid, text, t=10, photo=False):
    return {"id": rid, "reporterId": rid, "sector": "G-10", "t": t, "hasPhoto": photo, "ai": interpret(text, photo)}


def flat(tti, n=20):
    return [{"t": i, "tti": tti} for i in range(n)]


def test_roman_urdu_is_understood():
    assert interpret("Pani bhar gaya hai")["floodEvidence"] >= 0.8
    assert interpret("Gaari phans gayi hai underpass ke paas")["hazard"] == "vehicle_stranded"
    assert interpret("Motorcyclist gir gaya, madad karo")["peopleAtRisk"] is True
    assert interpret("Road band hai")["hazard"] == "road_blocked"
    assert interpret("pani nahi hai, sab theek hai")["floodEvidence"] < 0.3


def test_single_report_alone_stays_watch_low_confidence():
    a = assess("G-10", [rep("a", "Pani bhar gaya hai")], {"alert": "none", "rainMmHr": 0}, flat(1), 1, 12)
    assert a["severity"] == "Watch"
    assert a["confidence"] == "Low"


def test_weather_and_traffic_without_reports_reach_moderate_with_medium_confidence():
    a = assess("G-10", [], {"alert": "red", "rainMmHr": 60}, flat(2.8), 1, 19)
    assert a["score"] >= 30
    assert a["confidence"] == "Medium"


def test_three_corroborating_signals_give_critical_high_confidence():
    reports = [rep("a", "Pani bhar gaya hai ghutno tak"), rep("b", "Gaari phans gayi hai"),
               rep("c", "Road band hai underpass pe"), rep("d", "Motorcyclist gir gaya, madad!")]
    a = assess("G-10", reports, {"alert": "red", "rainMmHr": 62}, flat(3.5), 1, 15)
    assert a["severity"] == "Critical"
    assert a["confidence"] == "High"
    assert "4 citizen reports + active red rainfall alert" in a["explanation"]
    assert "traffic spike" in a["explanation"]
    assert a["breakdown"]["corroboration"] == 15
    assert a["breakdown"]["hazard"] == 10


def test_duplicate_reports_from_same_person_count_once():
    one = assess("G-10", [rep("a", "Pani bhar gaya hai")], None, [], 1, 12)
    spam = assess("G-10", [dict(rep("a", "Pani bhar gaya hai"), id=f"x{i}") for i in range(5)], None, [], 1, 12)
    assert one["score"] == spam["score"]


def test_old_reports_expire():
    a = assess("G-10", [rep("a", "Pani bhar gaya hai", t=0)], None, [], 1, 45)
    assert a["signals"]["citizen"]["count"] == 0
