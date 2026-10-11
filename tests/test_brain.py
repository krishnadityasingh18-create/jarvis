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


def test_abbreviations_and_list_numbers_do_not_split():
    assert run(Splitter(), "Good evening, Dr. Rao is here. Fine.") == \
        [("speak", "Good evening, Dr. Rao is here."), ("speak", "Fine.")]
    assert run(Splitter(), "Use e.g. a MOSFET here. Done.")[0] == \
        ("speak", "Use e.g. a MOSFET here.")
    assert run(Splitter(), "Steps:\n1. foo\n2. bar")[:2] == \
        [("speak", "Steps:"), ("speak", "1. foo")]


async def test_history_includes_screen_detail_and_skips_empty(cfg, conn, monkeypatch):
    db.add_message(conn, "user", "explain")
    db.add_message(conn, "assistant", "Short.", screen="long detail")
    db.add_message(conn, "assistant", "", interrupted=True)
    seen = []

    def fake(cfg, conn, messages, route, **k):
        seen.extend(messages)
        return iter([("route", "fast"), ("text", "Ok.")])
    monkeypatch.setattr(brain, "stream_reply", fake)

    async def speak(s):
        pass
    await brain.run_turn(cfg, conn, "step 3?", "fast", noop_emit, speak)
    assert {"role": "assistant", "content": "Short.\n---\nlong detail"} in seen
    assert all(m["content"] for m in seen)


async def test_unexpected_error_ends_turn_cleanly(cfg, conn, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("weird")
        yield
    monkeypatch.setattr(brain, "stream_reply", boom)
    said, events = [], []

    async def speak(s):
        said.append(s)

    async def emit(m):
        events.append(m)
    await brain.run_turn(cfg, conn, "hi", "fast", emit, speak)
    assert said == [brain.NO_MODEL] and events[-1]["type"] == "turn_end"


async def test_cancel_during_budget_warning_still_ends_turn(cfg, conn, monkeypatch):
    monkeypatch.setattr(brain, "stream_reply",
                        lambda *a, **k: iter([("route", "fast"), ("text", "Ok.")]))
    monkeypatch.setattr(brain, "_warned_on", None)
    db.record_usage(conn, "x", "m", 0, 0, 250)
    events = []

    async def speak(s):
        if s == brain.BUDGET:
            await asyncio.Event().wait()

    async def emit(m):
        events.append(m)
    t = asyncio.create_task(brain.run_turn(cfg, conn, "a", "fast", emit, speak))
    await asyncio.sleep(0.2)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert events[-1]["type"] == "turn_end"


async def test_sentences_prepared_as_they_arrive(cfg, conn, monkeypatch):
    monkeypatch.setattr(brain, "stream_reply",
                        lambda *a, **k: iter([("route", "fast"), ("text", "First. Second.")]))
    prepared, state = [], {}

    async def speak(s):
        if s == "First.":
            await asyncio.sleep(0.05)
            state["prepared_while_first_plays"] = list(prepared)

    await brain.run_turn(cfg, conn, "hi", "fast", noop_emit, speak, prepare=prepared.append)
    assert state["prepared_while_first_plays"] == ["First.", "Second."]


def scripted(*steps, seen=None):
    """A fake stream_reply that plays one list of events per call (one call per model step)."""
    it = iter(steps)

    def fake(cfg, conn, messages, route, **k):
        if seen is not None:
            seen.append({"messages": [dict(m) for m in messages], "route": route, **k})
        return iter(next(it))
    return fake


def tc(name, **args):
    import json
    return ("tool_calls", [{"id": "c1", "name": name, "arguments": json.dumps(args)}])


async def test_tool_step_then_text(cfg, conn, monkeypatch):
    seen = []
    monkeypatch.setattr(brain, "stream_reply", scripted(
        [("route", "fast"), ("text", "Noted, sir.\n"), tc("remember", text="lab on Thursdays")],
        [("route", "fast"), ("text", "I'll remember that, sir.")], seen=seen))
    said = []

    async def speak(s):
        said.append(s)
    await brain.run_turn(cfg, conn, "remember my lab is on Thursdays", "fast", noop_emit, speak)
    assert [m["text"] for m in db.memories(conn)] == ["lab on Thursdays"]
    assert said == ["Noted, sir.", "I'll remember that, sir."]
    step2 = seen[1]["messages"]
    assert step2[-2]["role"] == "assistant" and step2[-2]["tool_calls"][0]["function"]["name"] == "remember"
    assert step2[-1] == {"role": "tool", "tool_call_id": "c1", "content": "Remembered as [1]."}
    assert "remember" in db.recent_messages(conn)[-1]["actions"]
    assert seen[0]["tools"] and seen[0]["route"] == "fast"


async def test_five_step_cap_says_stopped(cfg, conn, monkeypatch):
    monkeypatch.setattr(brain, "stream_reply",
                        scripted(*[[("route", "fast"), tc("list_windows")]] * 5))
    monkeypatch.setitem(__import__("jarvis.tools", fromlist=["TOOLS"]).TOOLS, "list_windows",
                        __import__("jarvis.tools", fromlist=["Tool"]).Tool(
                            "list_windows", lambda ctx: ("none", None), "x", {"type": "object"}))
    said = []

    async def speak(s):
        said.append(s)
    await brain.run_turn(cfg, conn, "loop", "fast", noop_emit, speak)
    assert said == [brain.STOPPED]


async def test_history_replays_past_actions_as_tool_calls(cfg, conn, monkeypatch):
    # Text notes like "(actions: ...)" get imitated by the model instead of calling tools,
    # so past actions go back as real tool-call and tool-result messages.
    seen = []
    monkeypatch.setattr(brain, "stream_reply", scripted(
        [("route", "fast"), tc("remember", text="lab on Thursdays")],
        [("route", "fast"), ("text", "Noted, sir.")],
        [("route", "fast"), ("text", "Ok.")], seen=seen))

    async def speak(s):
        pass
    await brain.run_turn(cfg, conn, "remember lab", "fast", noop_emit, speak)
    await brain.run_turn(cfg, conn, "again", "fast", noop_emit, speak)
    msgs = seen[2]["messages"]
    i = next(i for i, m in enumerate(msgs) if m.get("tool_calls"))
    call = msgs[i]["tool_calls"][0]
    assert call["function"] == {"name": "remember", "arguments": '{"text": "lab on Thursdays"}'}
    assert msgs[i + 1] == {"role": "tool", "tool_call_id": call["id"],
                           "content": "Remembered as [1]."}
    assert msgs[i + 2] == {"role": "assistant", "content": "Noted, sir."}
    assert not any("(actions" in str(m.get("content")) for m in msgs)


async def test_memory_block_in_system_prompt(cfg, conn, monkeypatch):
    db.add_memory(conn, "lab on Thursdays")
    seen = []
    monkeypatch.setattr(brain, "stream_reply", scripted([("route", "fast"), ("text", "Ok.")],
                                                        seen=seen))

    async def speak(s):
        pass
    await brain.run_turn(cfg, conn, "when is lab", "fast", noop_emit, speak)
    assert "[1] lab on Thursdays" in seen[0]["messages"][0]["content"]


async def test_delegate_runs_agent_with_its_route_and_no_fallback(cfg, conn, monkeypatch):
    seen = []
    monkeypatch.setattr(brain, "stream_reply", scripted(
        [("route", "fast"), tc("delegate", agent="private", task="which PDFs about motors?")],
        [("route", "local"), ("text", "You have motor.pdf.")],
        [("route", "fast"), ("text", "You have one, sir.")], seen=seen))
    events = []

    async def emit(m):
        events.append(m)

    async def speak(s):
        pass
    await brain.run_turn(cfg, conn, "ask private", "fast", emit, speak)
    agent_call = seen[1]
    assert agent_call["route"] == "local" and agent_call["fallback"] is False
    assert agent_call["messages"][0]["content"] == cfg.agents["private"].prompt
    assert [t["function"]["name"] for t in agent_call["tools"]] == ["find_files", "open_path"]
    assert seen[2]["messages"][-1]["content"] == "You have motor.pdf."
    assert {"type": "agent", "agent": "private"} in events


async def test_delegate_unknown_agent(cfg, conn, monkeypatch):
    seen = []
    monkeypatch.setattr(brain, "stream_reply", scripted(
        [("route", "fast"), tc("delegate", agent="wizard", task="x")],
        [("route", "fast"), ("text", "Sorry, sir.")], seen=seen))

    async def speak(s):
        pass
    await brain.run_turn(cfg, conn, "ask wizard", "fast", noop_emit, speak)
    assert "no agent called wizard" in seen[1]["messages"][-1]["content"]


async def test_speech_drains_before_tool_runs(cfg, conn, monkeypatch):
    import asyncio
    order = []
    monkeypatch.setattr(brain, "stream_reply", scripted(
        [("route", "fast"), ("text", "One moment, sir.\n"), tc("remember", text="x")],
        [("route", "fast"), ("text", "Done.")]))
    real_add = db.add_memory
    monkeypatch.setattr(db, "add_memory", lambda c, t: (order.append("tool"), real_add(c, t))[1])

    async def speak(s):
        await asyncio.sleep(0.05)
        order.append(s)
    await brain.run_turn(cfg, conn, "x", "fast", noop_emit, speak)
    assert order == ["One moment, sir.", "tool", "Done."]


def test_separator_glued_to_sentence():
    sp = Splitter()
    out = run(sp, "It ran, sir.---\n", "```\nhello\n```")
    assert out == [("speak", "It ran, sir."), ("screen", "```\nhello\n```")]


def test_escaped_separator_from_model():
    # gpt-oss sometimes writes the two characters backslash-n instead of a newline
    sp = Splitter()
    out = run(sp, "Done, sir.---\\n```bash\\n", "hello\\n```")
    assert out[0] == ("speak", "Done, sir.")
    assert "".join(v for k, v in out[1:] if k == "screen") == "```bash\nhello\n```"


def test_history_never_starts_with_assistant(conn):
    import json
    db.add_message(conn, "assistant", "Done.", actions=json.dumps(
        [{"name": "remember", "arguments": "{}", "result": "ok"}]))
    db.add_message(conn, "user", "hi")
    db.add_message(conn, "assistant", "Hello.")
    h = brain._history(conn)
    assert h[0] == {"role": "user", "content": "hi"}
