import asyncio
from datetime import datetime, time

from jarvis.app import Hub
from jarvis.tools import outbound

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


class FakeVoice:
    def __init__(self, state="idle", loaded=True):
        from jarvis.voice import State
        self.fsm = type("F", (), {"state": State(state)})()
        self.loaded, self.events, self.said = loaded, [], []

    def event(self, name):
        self.events.append(name)

    async def speak(self, text):
        self.said.append(text)

    def prepare(self, text):
        pass


def test_not_ready_until_voice_loaded_and_ui_connected(cfg, conn):
    h = hub(cfg, conn)
    h.voice = FakeVoice(loaded=False)
    assert not h.ready(elapsed=5)
    h.voice.loaded = True
    assert not h.ready(elapsed=5)          # no window connected yet
    h.sockets.add(object())
    assert h.ready(elapsed=5)
    h.sockets.clear()
    assert h.ready(elapsed=200)            # give up waiting for the window after a while


async def test_scheduler_waits_while_voice_busy(cfg, conn):
    h = hub(cfg, conn)
    h.voice = FakeVoice("listening")
    reminder(conn, "2026-10-12T07:00")
    await h.tick(MON_0700)
    assert not h.sent and h.voice.said == []


async def test_briefing_turn_tells_voice_it_started(cfg, conn):
    cfg.briefing_enabled, cfg.briefing_time = True, time(7, 30)
    h = hub(cfg, conn)
    h.voice = FakeVoice("idle")
    await h.tick(datetime(2026, 10, 12, 7, 45))
    await asyncio.sleep(0)
    assert "text_input" in h.voice.events and h.turns == [h.BRIEFING_PROMPT]


async def test_no_morning_briefing_in_the_evening(cfg, conn):
    cfg.briefing_enabled, cfg.briefing_time = True, time(7, 30)
    h = hub(cfg, conn)
    await h.tick(datetime(2026, 10, 12, 23, 0))
    await asyncio.sleep(0)
    assert h.turns == []


async def test_call_reminder_phones_my_number_only_when_on_time(cfg, conn, monkeypatch):
    rang = []
    monkeypatch.setattr(outbound, "ring_me", lambda cfg, conn, reason: rang.append(reason))
    h = hub(cfg, conn)
    conn.execute("INSERT INTO reminders(at, text, call) VALUES('2026-10-12T05:00', 'late', 1)")
    conn.execute("INSERT INTO reminders(at, text, call) VALUES('2026-10-12T07:00', 'lab', 1)")
    await h.tick(MON_0700)
    assert rang == ["A reminder: lab"]
    assert [m["text"] for m in h.sent] == ["Reminder (late): late", "Reminder: lab"]


async def test_call_reminder_without_remote_setup_still_fires(cfg, conn, monkeypatch):
    monkeypatch.setattr(outbound, "get_key", lambda n: None)
    cfg.remote = {}
    h = hub(cfg, conn)
    conn.execute("INSERT INTO reminders(at, text, call) VALUES('2026-10-12T07:00', 'lab', 1)")
    await h.tick(MON_0700)
    texts = [m["text"] for m in h.sent]
    assert texts[0] == "Reminder: lab" and texts[1].startswith("Couldn't phone you about it: ")
