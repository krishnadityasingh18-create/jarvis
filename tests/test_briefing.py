import json
from datetime import datetime

import pytest

from jarvis.tools import TOOLS, briefing, day, github, run
from tests.test_tools import call, make_ctx

NOW = datetime(2026, 10, 12, 7, 30)   # Monday


@pytest.fixture(autouse=True)
def fixed(monkeypatch):
    monkeypatch.setattr(day, "now", lambda: NOW)


@pytest.fixture
def sources(monkeypatch):
    monkeypatch.setattr(briefing, "_weather_json", lambda cfg: {
        "temperature_2m_max": [33.4], "temperature_2m_min": [26.0],
        "precipitation_probability_max": [60], "weather_code": [61]})

    def gh(*args):
        if args[0] == "api":
            return "3"
        kind = args[1]
        return json.dumps([{"title": f"{kind} {args[2]}", "url": "https://github.com/x",
                            "repository": {"nameWithOwner": "me/repo"}}])
    monkeypatch.setattr(github, "_gh", gh)


async def test_briefing_gathers_everything(cfg, conn, sources):
    conn.execute("INSERT INTO timetable(day,start,end,subject,room) VALUES(0,'09:00','09:50',"
                 "'Physics','TP-201'),(1,'10:00','10:50','Maths','')")
    conn.execute("INSERT INTO tasks(title,due) VALUES('Lab report','2026-10-14'),"
                 "('Old essay','2026-10-01'),('Far away','2026-12-01')")
    conn.execute("INSERT INTO reminders(at,text) VALUES('2026-10-12T18:00','gym')")
    r = await run(make_ctx(cfg, conn), call("daily_briefing"))
    assert "Monday 12 October 2026" in r
    assert "33°C" in r and "26°C" in r and "rain" in r and "60%" in r
    assert "09:00-09:50 Physics (TP-201)" in r and "Maths" not in r
    assert "Lab report" in r and "Old essay" in r and "OVERDUE" in r and "Far away" not in r
    assert "18:00 gym" in r
    assert "review-requested" in r and "3 unread" in r
    assert "Google isn't connected" in r
    assert "set_plan" in r                                # tells the model to save the plan


async def test_briefing_survives_every_source_failing(cfg, conn, monkeypatch):
    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(briefing, "_weather_json", boom)
    monkeypatch.setattr(github, "_gh", boom)
    r = await run(make_ctx(cfg, conn), call("daily_briefing"))
    assert "Weather: unavailable" in r and "GitHub: unavailable" in r
    assert "No classes today" in r


async def test_github_activity_tool(cfg, conn, sources):
    r = await run(make_ctx(cfg, conn), call("github_activity"))
    assert "me/repo" in r and r.startswith('<untrusted source="github_activity">')
    assert TOOLS["github_activity"].readonly and TOOLS["daily_briefing"].readonly


@pytest.mark.parametrize("text", ["brief me", "Give me my morning briefing.",
                                  "what's my daily briefing?"])
async def test_briefing_request_gathers_real_data_first(cfg, conn, sources, monkeypatch, text):
    from jarvis import brain
    from tests.test_brain import noop_emit, scripted
    seen = []
    monkeypatch.setattr(brain, "stream_reply", scripted(
        [("route", "fast"), ("text", "Good morning, sir.")], seen=seen))

    async def speak(s):
        pass
    await brain.run_turn(cfg, conn, text, "fast", noop_emit, speak)
    msgs = seen[0]["messages"]
    assert msgs[-2]["tool_calls"][0]["function"]["name"] == "daily_briefing"
    assert "Weather:" in msgs[-1]["content"] and msgs[-1]["role"] == "tool"


def test_not_a_briefing_request():
    from jarvis import brain
    assert not brain.wants_briefing("what is a debriefing in psychology")
