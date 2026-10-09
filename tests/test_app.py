import asyncio
import time

from fastapi.testclient import TestClient

from jarvis.app import create_app


def recv_until(ws, pred, limit=20):
    seen = []
    for _ in range(limit):
        m = ws.receive_json()
        seen.append(m)
        if pred(m):
            return seen
    raise AssertionError(f"never matched, saw {seen}")


def connect(client):
    ws = client.websocket_connect("/ws")
    w = ws.__enter__()
    assert w.receive_json()["type"] == "history"
    assert w.receive_json()["type"] == "usage"
    return ws, w


def test_text_input_runs_turn_and_streams_events(cfg, conn):
    calls = []

    async def fake(cfg, conn, text, route, emit, speak):
        calls.append((text, route))
        await emit({"type": "spoken_sentence", "text": "Hi."})
        await emit({"type": "turn_end"})

    with TestClient(create_app(cfg, conn, turn_runner=fake)) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "text_input", "text": "hello"})
        types = [ws.receive_json()["type"] for _ in range(4)]
        ctx.__exit__(None, None, None)
    assert types == ["user_text", "spoken_sentence", "turn_end", "usage"]
    assert calls == [("hello", "fast")]


def test_empty_text_ignored(cfg, conn):
    calls = []

    async def fake(cfg, conn, text, route, emit, speak):
        calls.append(text)
        await emit({"type": "turn_end"})

    with TestClient(create_app(cfg, conn, turn_runner=fake)) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "text_input", "text": "   "})
        ws.send_json({"type": "text_input", "text": "hi"})
        first = ws.receive_json()
        recv_until(ws, lambda m: m["type"] == "turn_end")
        ctx.__exit__(None, None, None)
    assert first == {"type": "user_text", "text": "hi"}
    assert calls == ["hi"]


def test_new_text_cancels_running_turn(cfg, conn):
    log = []

    async def fake(cfg, conn, text, route, emit, speak):
        log.append(("start", text))
        if text == "a":
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                log.append(("cancelled", text))
                await emit({"type": "turn_end"})
                raise
        await emit({"type": "turn_end"})

    with TestClient(create_app(cfg, conn, turn_runner=fake)) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "text_input", "text": "a"})
        recv_until(ws, lambda m: m == {"type": "user_text", "text": "a"})
        ws.send_json({"type": "text_input", "text": "b"})
        recv_until(ws, lambda m: m == {"type": "user_text", "text": "b"})
        recv_until(ws, lambda m: m["type"] == "turn_end")
        ctx.__exit__(None, None, None)
    assert log[:3] == [("start", "a"), ("cancelled", "a"), ("start", "b")]


def test_cancel_message_stops_turn(cfg, conn):
    log = []

    async def fake(cfg, conn, text, route, emit, speak):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            log.append("cancelled")
            await emit({"type": "turn_end"})
            raise

    with TestClient(create_app(cfg, conn, turn_runner=fake)) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "text_input", "text": "a"})
        recv_until(ws, lambda m: m["type"] == "user_text")
        ws.send_json({"type": "cancel"})
        recv_until(ws, lambda m: m["type"] == "turn_end")
        ctx.__exit__(None, None, None)
    assert log == ["cancelled"]


def test_set_route_used_for_next_turn(cfg, conn):
    calls = []

    async def fake(cfg, conn, text, route, emit, speak):
        calls.append(route)
        await emit({"type": "turn_end"})

    with TestClient(create_app(cfg, conn, turn_runner=fake)) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "set_route", "route": "local"})
        ws.send_json({"type": "text_input", "text": "hi"})
        recv_until(ws, lambda m: m["type"] == "turn_end")
        ws.send_json({"type": "text_input", "text": "think carefully: why"})
        recv_until(ws, lambda m: m["type"] == "turn_end")
        ctx.__exit__(None, None, None)
    assert calls == ["local", "reasoning"]


def test_history_sent_on_connect(cfg, conn):
    from jarvis import db
    db.add_message(conn, "user", "earlier")

    async def fake(*a):
        pass

    with TestClient(create_app(cfg, conn, turn_runner=fake)) as client:
        with client.websocket_connect("/ws") as ws:
            h = ws.receive_json()
    assert h["type"] == "history" and h["messages"][-1]["content"] == "earlier"


def test_index_served(cfg, conn):
    with TestClient(create_app(cfg, conn)) as client:
        r = client.get("/")
    assert r.status_code == 200 and "JARVIS" in r.text


def test_typing_while_listening_runs_typed_turn(cfg, conn, monkeypatch):
    import sounddevice

    from jarvis.voice import VoiceLoop
    for name in ("play", "wait", "stop"):
        monkeypatch.setattr(sounddevice, name, lambda *a, **k: None)
    log = []

    async def fake(cfg, conn, text, route, emit, speak, prepare=None):
        log.append(("start", text))
        try:
            await asyncio.sleep(0.3)   # long enough for any stray cancel to land
        except asyncio.CancelledError:
            log.append(("cancelled", text))
            await emit({"type": "turn_end"})
            raise
        log.append(("done", text))
        await emit({"type": "turn_end"})

    voice = VoiceLoop(cfg, lambda n, p: None)
    voice._do_chime = lambda: None
    app = create_app(cfg, conn, voice=voice, turn_runner=fake)
    voice.on_event = app.state.hub.from_thread
    with TestClient(app) as client:
        ctx, ws = connect(client)
        voice.event("wake")                      # a (false) wake: now LISTENING
        ws.send_json({"type": "text_input", "text": "typed question"})
        time.sleep(0.8)   # a buggy cancel can kill the turn before it starts, so no turn_end to wait on
        ctx.__exit__(None, None, None)
    assert log == [("start", "typed question"), ("done", "typed question")]


def test_foreign_origin_rejected(cfg, conn):
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with TestClient(create_app(cfg, conn)) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws", headers={"origin": "http://evil.example"}) as ws:
                ws.receive_json()
