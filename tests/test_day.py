from datetime import datetime

import pytest

from jarvis import brain, db
from jarvis.tools import day, run, undo
from tests.test_brain import noop_emit, scripted
from tests.test_tools import call, make_ctx

NOW = datetime(2026, 10, 12, 7, 30)   # a Monday


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(day, "now", lambda: NOW)


def last(conn):
    return db.recent_actions(conn)[0]


async def test_timetable_add_list_remove_undo(cfg, conn):
    ctx = make_ctx(cfg, conn)
    r = await run(ctx, call("add_class", day="Monday", start="09:00", end="09:50",
                            subject="Physics", room="TP-201"))
    assert r.startswith("Added")
    await run(ctx, call("add_class", day="tue", start="10:00", end="10:50", subject="Maths"))
    listing = await run(ctx, call("list_timetable"))
    assert "Monday" in listing and "09:00-09:50 Physics (TP-201)" in listing and "Maths" in listing
    cid = conn.execute("SELECT id FROM timetable WHERE subject='Physics'").fetchone()[0]
    await run(ctx, call("remove_class", class_id=cid))
    assert "Physics" not in await run(ctx, call("list_timetable"))
    undo(conn, [a for a in db.recent_actions(conn) if a["tool"] == "remove_class"][0]["id"])
    assert "Physics" in await run(ctx, call("list_timetable"))


async def test_bad_day_or_time_refused(cfg, conn):
    r = await run(make_ctx(cfg, conn), call("add_class", day="Funday", start="9", end="10",
                                            subject="X"))
    assert r.startswith("Error")


async def test_tasks_add_complete_undo(cfg, conn):
    ctx = make_ctx(cfg, conn)
    await run(ctx, call("add_task", title="Lab report", due="2026-10-15"))
    tid = conn.execute("SELECT id FROM tasks").fetchone()[0]
    await run(ctx, call("complete_task", task_id=tid))
    assert "Lab report" not in await run(ctx, call("list_tasks"))
    undo(conn, [a for a in db.recent_actions(conn) if a["tool"] == "complete_task"][0]["id"])
    assert "Lab report (due Wed 15 Oct)" in await run(ctx, call("list_tasks"))
    undo(conn, [a for a in db.recent_actions(conn) if a["tool"] == "add_task"][0]["id"])
    assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0


async def test_remind_validates_and_undo(cfg, conn):
    ctx = make_ctx(cfg, conn)
    r = await run(ctx, call("remind", at="2026-10-12T09:15", text="call mom"))
    assert "09:15" in r
    assert (await run(ctx, call("remind", at="2026-10-11T09:15", text="x"))).startswith("Error")
    assert (await run(ctx, call("remind", at="tomorrow", text="x"))).startswith("Error")
    undo(conn, [a for a in db.recent_actions(conn) if a["status"] == "done"][0]["id"])
    assert conn.execute("SELECT count(*) FROM reminders").fetchone()[0] == 0


async def test_set_plan_and_undo(cfg, conn):
    ctx = make_ctx(cfg, conn)
    await run(ctx, call("set_plan", text="9 Physics; 4 gym"))
    await run(ctx, call("set_plan", text="9 Physics; 6 gym"))
    assert day.today_plan(conn) == "9 Physics; 6 gym"
    undo(conn, last(conn)["id"])
    assert day.today_plan(conn) == "9 Physics; 4 gym"


async def test_prompt_has_now_and_plan(cfg, conn, monkeypatch):
    db.add_message(conn, "user", "x")
    conn.execute("INSERT INTO plans(day, text) VALUES('2026-10-12', 'gym at 6')")
    seen = []
    monkeypatch.setattr(brain, "stream_reply", scripted([("route", "fast"), ("text", "Ok.")],
                                                        seen=seen))

    async def speak(s):
        pass
    await brain.run_turn(cfg, conn, "hi", "fast", noop_emit, speak)
    system = seen[0]["messages"][0]["content"]
    assert "Now: Monday 12 October 2026, 07:30" in system and "Today's plan: gym at 6" in system
