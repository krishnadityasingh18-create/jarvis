import asyncio
from datetime import date

import pytest

from jarvis import brain, db
from jarvis.brain import Splitter, pick_route
from jarvis.llm import AllRoutesFailed


def run(sp, *chunks):
    return [e for c in chunks for e in sp.feed(c)] + sp.flush()


async def noop_emit(msg):
    pass


def test_two_spoken_then_screen():
    assert run(Splitter(), "Hello sir. It is 5 pm.\n---\n# Detail\nx") == \
        [("speak", "Hello sir."), ("speak", "It is 5 pm."), ("screen", "# Detail\nx")]


def test_no_separator_third_sentence_goes_to_screen():
    ev = run(Splitter(), "One. Two. Three.")
    assert ev[:2] == [("speak", "One."), ("speak", "Two.")] and ev[2] == ("screen", "Three.")


def test_separator_split_across_chunks():
    ev = run(Splitter(), "Hi sir.\n-", "--\nbody")
    assert ("screen", "body") in ev
    assert all(t != "speak" or "-" not in s for t, s in ev)


def test_decimal_not_a_sentence_break():
    assert run(Splitter(), "The cutoff is 3.5 kHz. Done.") == \
        [("speak", "The cutoff is 3.5 kHz."), ("speak", "Done.")]


def test_token_by_token_stream():
    text = "Good evening, sir. Systems are online.\n---\n## Status\n- all good"
    ev = run(Splitter(), *text)
    assert [e for e in ev if e[0] == "speak"] == \
        [("speak", "Good evening, sir."), ("speak", "Systems are online.")]
    assert "".join(s for t, s in ev if t == "screen") == "## Status\n- all good"


def test_pick_route():
    assert pick_route("Think carefully: BJT vs MOSFET", "fast") == "reasoning"
    assert pick_route("hello", "local") == "local"


async def test_turn_speaks_saves_and_emits(cfg, conn, monkeypatch):
    monkeypatch.setattr(brain, "stream_reply",
                        lambda *a, **k: iter([("route", "fast"), ("text", "Hi sir.\n---\nmore")]))
    said, events = [], []

    async def speak(s):
        said.append(s)

    async def emit(m):
        events.append(m)

    await brain.run_turn(cfg, conn, "hi", "fast", emit, speak)
    assert said == ["Hi sir."]
    last = db.recent_messages(conn)[-1]
    assert (last["role"], last["content"], last["screen"]) == ("assistant", "Hi sir.", "more")
    types = [e["type"] for e in events]
    assert types[-1] == "turn_end" and "spoken_sentence" in types and "screen_delta" in types


async def test_interrupted_turn_saves_only_spoken(cfg, conn, monkeypatch):
    monkeypatch.setattr(brain, "stream_reply",
                        lambda *a, **k: iter([("route", "fast"), ("text", "First. Second.")]))
    gate, said = asyncio.Event(), []

    async def speak(s):
        said.append(s)
        if len(said) == 2:
            await gate.wait()

    t = asyncio.create_task(brain.run_turn(cfg, conn, "hi", "fast", noop_emit, speak))
    await asyncio.sleep(0.1)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    last = db.recent_messages(conn)[-1]
    assert last["content"] == "First." and last["interrupted"] == 1


async def test_all_routes_failed_speaks_copy(cfg, conn, monkeypatch):
    def boom(*a, **k):
        raise AllRoutesFailed("fast: no API key")
        yield
    monkeypatch.setattr(brain, "stream_reply", boom)
    said, events = [], []

    async def speak(s):
        said.append(s)

    async def emit(m):
        events.append(m)

    await brain.run_turn(cfg, conn, "hi", "fast", emit, speak)
    assert said == ["I can't reach any model right now, sir."]
    assert {"type": "banner", "text": "fast: no API key"} in events


async def test_fallback_route_shows_banner(cfg, conn, monkeypatch):
    monkeypatch.setattr(brain, "stream_reply",
                        lambda *a, **k: iter([("route", "local"), ("text", "Hi.")]))
    events = []

    async def emit(m):
        events.append(m)

    async def speak(s):
        pass

    await brain.run_turn(cfg, conn, "hi", "fast", emit, speak)
    assert {"type": "banner", "text": "fast unavailable, answered via local"} in events


async def test_budget_warning_spoken_once_per_day(cfg, conn, monkeypatch):
    monkeypatch.setattr(brain, "stream_reply",
                        lambda *a, **k: iter([("route", "fast"), ("text", "Ok.")]))
    monkeypatch.setattr(brain, "_warned_on", None)
    db.record_usage(conn, "x", "m", 0, 0, 250)
    said = []

    async def speak(s):
        said.append(s)

    await brain.run_turn(cfg, conn, "a", "fast", noop_emit, speak)
    await brain.run_turn(cfg, conn, "b", "fast", noop_emit, speak)
    assert said.count("Sir, we've used 80 percent of this month's budget.") == 1
    assert brain._warned_on == date.today()
