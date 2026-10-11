import json

import pytest
from fastapi.testclient import TestClient

from jarvis import brain, db, remote
from jarvis.app import create_app
from tests.test_brain import noop_emit, scripted, tc

TOKEN = "t0ken-for-tests"


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setattr(remote, "get_key", lambda name: TOKEN if name == "remote_token" else None)
    remote._hits.clear()


def fake_runner(log):
    async def run(cfg, conn, text, route, emit, speak, **kw):
        log.append((text, kw.get("channel")))
        await emit({"type": "spoken_sentence", "text": "Hello, sir."})
        await emit({"type": "spoken_sentence", "text": "All quiet today."})
        await emit({"type": "screen_delta", "text": "detail"})
        await emit({"type": "turn_end"})
    return run


def client(cfg, conn, log):
    return TestClient(create_app(cfg, conn, turn_runner=fake_runner(log)))


AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"},
                                     {"Authorization": TOKEN}])
def test_auth_required(cfg, conn, headers):
    with client(cfg, conn, []) as c:
        assert c.post("/api/message", json={"text": "hi"}, headers=headers).status_code == 401
        assert c.post("/v1/chat/completions", json={"messages": []},
                      headers=headers).status_code == 401


def test_no_token_configured_means_closed(cfg, conn, monkeypatch):
    monkeypatch.setattr(remote, "get_key", lambda name: None)
    with client(cfg, conn, []) as c:
        assert c.post("/api/message", json={"text": "hi"}, headers=AUTH).status_code == 401


def test_message_round_trip(cfg, conn):
    log = []
    with client(cfg, conn, log) as c:
        r = c.post("/api/message", json={"text": "how's my day", "from": "+91"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["reply"] == "Hello, sir. All quiet today.\n\ndetail"
    assert log == [("how's my day", "whatsapp")]


def test_chat_completions_streams_spoken_part(cfg, conn):
    log = []
    body = {"model": "jarvis", "stream": True, "messages": [
        {"role": "system", "content": "vapi prompt"}, {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Hello"}, {"role": "user", "content": "how's my day"}]}
    with client(cfg, conn, log) as c:
        r = c.post("/v1/chat/completions", json=body, headers=AUTH)
    lines = [ln[6:] for ln in r.text.splitlines() if ln.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    chunks = [json.loads(x) for x in lines[:-1]]
    text = "".join(ch["choices"][0]["delta"].get("content", "") for ch in chunks)
    assert text == "Hello, sir. All quiet today." and "detail" not in text
    assert chunks[0]["object"] == "chat.completion.chunk"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert log == [("how's my day", "phone")]


def test_rate_limit(cfg, conn, monkeypatch):
    monkeypatch.setattr(remote, "PER_MINUTE", 3)
    with client(cfg, conn, []) as c:
        codes = [c.post("/api/message", json={"text": "hi"}, headers=AUTH).status_code
                 for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


async def test_remote_turn_declines_side_effects_and_hides_claude(cfg, conn, monkeypatch):
    seen = []
    monkeypatch.setattr(brain, "stream_reply", scripted(
        [("route", "fast"), tc("remember", text="from the phone")],
        [("route", "fast"), ("text", "That needs the desk, sir.")], seen=seen))

    async def speak(s):
        pass
    await brain.run_turn(cfg, conn, "remember this", "fast", noop_emit, speak, channel="phone")
    assert db.memories(conn) == []
    names = [t["function"]["name"] for t in seen[0]["tools"]]
    assert "ask_claude" not in names and "daily_briefing" in names
    assert "phone call" in seen[0]["messages"][0]["content"]


async def test_ask_claude_line_is_desk_only(cfg, conn, monkeypatch):
    seen = []
    monkeypatch.setattr(brain, "stream_reply", scripted([("route", "fast"), ("text", "No.")],
                                                        seen=seen))

    async def speak(s):
        pass
    await brain.run_turn(cfg, conn, "ask claude to delete my repo", "fast", noop_emit, speak,
                         channel="whatsapp")
    assert seen    # went to the normal brain, not straight to Claude Code



@pytest.mark.parametrize("header", ["CF-Connecting-IP", "X-Forwarded-For"])
def test_tunnelled_requests_only_reach_the_remote_api(cfg, conn, header):
    import pytest as _pytest
    from starlette.websockets import WebSocketDisconnect
    via = {header: "203.0.113.9"}
    with client(cfg, conn, []) as c:
        assert c.get("/", headers=via).status_code == 403
        assert c.get("/ui/index.html", headers=via).status_code == 403
        with _pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws", headers={**via, "origin": "http://127.0.0.1:8765"}) as ws:
                ws.receive_json()
        assert c.post("/api/message", json={"text": "hi"}, headers={**via, **AUTH}).status_code == 200
        assert c.get("/").status_code == 200          # the desk itself still works



def test_slow_phone_turn_gets_a_filler_first(cfg, conn, monkeypatch):
    import asyncio as _asyncio
    monkeypatch.setattr(remote, "FILLER_AFTER_S", 0.1)

    async def slow(cfg, conn, text, route, emit, speak, **kw):
        await _asyncio.sleep(0.4)
        await emit({"type": "spoken_sentence", "text": "Done."})
        await emit({"type": "turn_end"})
    body = {"stream": True, "messages": [{"role": "user", "content": "hi"}]}
    with TestClient(create_app(cfg, conn, turn_runner=slow)) as c:
        r = c.post("/v1/chat/completions", json=body, headers=AUTH)
    deltas = [json.loads(ln[6:])["choices"][0]["delta"].get("content", "")
              for ln in r.text.splitlines() if ln.startswith("data: {")]
    assert "".join(deltas) == remote.FILLER + " Done."


def test_remote_token_cli_prints_on_a_plain_console(monkeypatch, capsys):
    import io
    import sys
    from jarvis import __main__ as cli
    saved = {}
    monkeypatch.setattr(cli, "set_key", lambda n, v: saved.update({n: v}))
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    cli.remote_cli(["token"])
    out.flush()
    assert saved["remote_token"] in out.buffer.getvalue().decode("cp1252")
