import asyncio
from datetime import datetime, time

from jarvis.app import Hub

MON_0700 = datetime(2026, 10, 12, 7, 0)


def hub(cfg, conn):
    turns = []

    async def runner(cfg, conn, text, route, emit, speak, **kw):
        turns.append(text)
        await emit({"type": "turn_end"})
    h = Hub(cfg, conn, None, runner)
    sent = []

    async def capture(msg):
        sent.append(msg)
    h.broadcast = capture
    h.turns, h.sent = turns, sent
    return h


def reminder(conn, at, text="call mom"):
    conn.execute("INSERT INTO reminders(at, text) VALUES(?,?)", (at, text))


async def test_due_reminder_fires_once(cfg, conn):
    h = hub(cfg, conn)
    reminder(conn, "2026-10-12T07:00")
    reminder(conn, "2026-10-12T09:00", "later")
    await h.tick(MON_0700)
    await h.tick(MON_0700)
    banners = [m["text"] for m in h.sent if m["type"] == "banner"]
    assert banners == ["Reminder: call mom"]
    assert conn.execute("SELECT done FROM reminders WHERE text='later'").fetchone()[0] == 0


async def test_missed_reminder_fires_late_after_restart(cfg, conn):
    h = hub(cfg, conn)
    reminder(conn, "2026-10-12T05:00")
    await h.tick(MON_0700)
    assert [m["text"] for m in h.sent if m["type"] == "banner"] == ["Reminder (late): call mom"]


async def test_reminder_waits_for_running_turn(cfg, conn):
    h = hub(cfg, conn)
    h.turn = asyncio.ensure_future(asyncio.sleep(10))
    reminder(conn, "2026-10-12T07:00")
    await h.tick(MON_0700)
    assert not h.sent
    h.turn.cancel()
    await h.tick(MON_0700)
    assert [m["type"] for m in h.sent] == ["banner"]


async def test_morning_briefing_once_a_day_after_time(cfg, conn):
    cfg.briefing_enabled, cfg.briefing_time = True, time(7, 30)
    h = hub(cfg, conn)
    await h.tick(MON_0700)                                  # too early
    await h.tick(datetime(2026, 10, 12, 7, 31))
    await asyncio.sleep(0)
    await h.tick(datetime(2026, 10, 12, 9, 0))              # already done today
    await asyncio.sleep(0)
    await h.tick(datetime(2026, 10, 13, 8, 0))              # next day
    await asyncio.sleep(0)
    assert h.turns == [h.BRIEFING_PROMPT] * 2


async def test_briefing_disabled(cfg, conn):
    cfg.briefing_enabled, cfg.briefing_time = False, time(7, 30)
    h = hub(cfg, conn)
    await h.tick(datetime(2026, 10, 12, 8, 0))
    await asyncio.sleep(0)
    assert h.turns == []


def test_config_briefing():
    from jarvis.config import load_config
    c = load_config()
    assert c.briefing_time == time(7, 30) and c.briefing_enabled is True
    assert round(c.latitude, 2) == 12.82 and round(c.longitude, 2) == 80.04
