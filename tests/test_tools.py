import json

import pytest

from jarvis import db, tools
from jarvis.tools import TOOLS, Ctx, Tool, parse_yes_no, run, undo
from jarvis.tools.memory import memory_block


def make_ctx(cfg, conn, answer=True, agent=None, allowed=None):
    asked, emitted = [], []

    async def confirm(q):
        asked.append(q)
        return answer

    async def emit(m):
        emitted.append(m)
    c = Ctx(cfg, conn, confirm, emit, agent=agent, allowed=allowed)
    c.asked, c.emitted = asked, emitted
    return c


def call(tool_name, **args):
    return {"id": "c1", "name": tool_name, "arguments": json.dumps(args)}


@pytest.fixture
def fake_tools(monkeypatch):
    ran = []

    def safe(ctx, x=""):
        ran.append(("safe", x))
        return f"did {x}", None

    def risky(ctx):
        ran.append(("risky",))
        return "boom done", None

    def broken(ctx):
        raise RuntimeError("disk on fire")

    def big(ctx):
        return "x" * 10000, None
    for t in (Tool("safe", safe, "a safe tool", {"type": "object", "properties": {}}),
              Tool("risky", risky, "a risky tool", {"type": "object", "properties": {}},
                   confirm=True, describe=lambda a: "do the risky thing"),
              Tool("broken", broken, "fails", {"type": "object", "properties": {}}),
              Tool("big", big, "big output", {"type": "object", "properties": {}})):
        monkeypatch.setitem(TOOLS, t.name, t)
    return ran


@pytest.mark.parametrize("text,ok", [("Yes please.", True), ("go ahead", True), ("Do it", True),
                                     ("no", False), ("yes, no", False), ("maybe", False),
                                     ("", False), ("Don't.", False), ("Okay.", True)])
def test_parse_yes_no(text, ok):
    assert parse_yes_no(text) is ok


async def test_run_unknown_tool(cfg, conn):
    assert "no tool named nope" in await run(make_ctx(cfg, conn), call("nope"))


async def test_run_bad_json(cfg, conn, fake_tools):
    r = await run(make_ctx(cfg, conn), {"id": "c", "name": "safe", "arguments": "{oops"})
    assert "not valid JSON" in r and fake_tools == []


async def test_confirm_declined_logs_declined_and_does_not_run(cfg, conn, fake_tools):
    ctx = make_ctx(cfg, conn, answer=False)
    assert await run(ctx, call("risky")) == tools.DECLINED
    assert fake_tools == [] and ctx.asked == ["Do the risky thing, sir?"]
    assert db.recent_actions(conn)[0]["status"] == "declined"
    assert ctx.emitted[-1]["type"] == "action"
    assert ctx.actions == [{"name": "risky", "arguments": "{}", "result": tools.DECLINED}]


async def test_confirm_accepted_runs(cfg, conn, fake_tools):
    ctx = make_ctx(cfg, conn)
    assert await run(ctx, call("risky")) == "boom done"
    assert fake_tools == [("risky",)] and db.recent_actions(conn)[0]["status"] == "done"


async def test_safe_tool_does_not_confirm(cfg, conn, fake_tools):
    ctx = make_ctx(cfg, conn)
    assert await run(ctx, call("safe", x="1")) == "did 1" and ctx.asked == []


async def test_failed_tool_logged_failed_with_error_text(cfg, conn, fake_tools):
    r = await run(make_ctx(cfg, conn), call("broken"))
    assert "disk on fire" in r and db.recent_actions(conn)[0]["status"] == "failed"


async def test_result_capped_4000(cfg, conn, fake_tools):
    assert len(await run(make_ctx(cfg, conn), call("big"))) == 4000


async def test_agent_cannot_call_unlisted_tool_or_delegate(cfg, conn, fake_tools):
    ctx = make_ctx(cfg, conn, agent="private", allowed=["safe"])
    assert "not available" in await run(ctx, call("risky"))
    assert "not available" in await run(ctx, call("delegate", agent="thinker", task="x"))
    assert fake_tools == []


async def test_agent_call_always_confirms(cfg, conn, fake_tools):
    ctx = make_ctx(cfg, conn, agent="private", allowed=["safe"])
    await run(ctx, call("safe", x="2"))
    assert len(ctx.asked) == 1 and ctx.asked[0].startswith("The private agent wants to ")
    assert db.recent_actions(conn)[0]["agent"] == "private"


async def test_remember_then_undo_deletes(cfg, conn):
    await run(make_ctx(cfg, conn), call("remember", text="lab on Thursdays"))
    assert [m["text"] for m in db.memories(conn)] == ["lab on Thursdays"]
    undo(conn, db.recent_actions(conn)[0]["id"])
    assert db.memories(conn) == [] and db.recent_actions(conn)[0]["status"] == "undone"


async def test_forget_then_undo_restores(cfg, conn):
    mid = db.add_memory(conn, "wifi is slow")
    await run(make_ctx(cfg, conn), call("forget", memory_id=mid))
    assert db.memories(conn) == []
    undo(conn, db.recent_actions(conn)[0]["id"])
    assert [m["text"] for m in db.memories(conn)] == ["wifi is slow"]


async def test_undo_twice_refused(cfg, conn):
    await run(make_ctx(cfg, conn), call("remember", text="a"))
    aid = db.recent_actions(conn)[0]["id"]
    undo(conn, aid)
    with pytest.raises(ValueError):
        undo(conn, aid)


async def test_undo_last_picks_latest_undoable(cfg, conn, fake_tools):
    ctx = make_ctx(cfg, conn)
    await run(ctx, call("remember", text="first"))
    await run(ctx, call("remember", text="second"))
    await run(ctx, call("safe"))                     # nothing to undo here
    r = await run(ctx, call("undo_last"))
    assert "remember" in r and [m["text"] for m in db.memories(conn)] == ["first"]


def test_memory_block_lists_ids(conn):
    assert memory_block(conn) == ""
    mid = db.add_memory(conn, "lab on Thursdays")
    assert f"[{mid}] lab on Thursdays" in memory_block(conn)


def test_schemas_shape():
    s = tools.schemas(["remember"])
    assert s[0]["type"] == "function" and s[0]["function"]["name"] == "remember"
    assert "text" in s[0]["function"]["parameters"]["properties"]


async def test_agent_never_delegates_even_if_configured(cfg, conn):
    ctx = make_ctx(cfg, conn, agent="private", allowed=["delegate"])
    r = await run(ctx, call("delegate", agent="thinker", task="x"))
    assert "not available" in r and ctx.asked == []
