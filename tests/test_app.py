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
    assert w.receive_json()["type"] == "actions"
    assert w.receive_json()["type"] == "chats"
    assert w.receive_json()["type"] == "mic"
    assert w.receive_json() == {"type": "whatsapp", "state": "unlinked", "qr": ""}
    assert [w.receive_json()["type"] for _ in range(3)] == ["vault", "artifacts", "glance"]
    return ws, w


def test_text_input_runs_turn_and_streams_events(cfg, conn):
    calls = []

    async def fake(cfg, conn, text, route, emit, speak, **kw):
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

    async def fake(cfg, conn, text, route, emit, speak, **kw):
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

    async def fake(cfg, conn, text, route, emit, speak, **kw):
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

    async def fake(cfg, conn, text, route, emit, speak, **kw):
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

    async def fake(cfg, conn, text, route, emit, speak, **kw):
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
    assert r.status_code == 200 and "Jarvis" in r.text


def test_typing_while_listening_runs_typed_turn(cfg, conn, monkeypatch):
    import sounddevice

    from jarvis.voice import VoiceLoop
    for name in ("play", "wait", "stop"):
        monkeypatch.setattr(sounddevice, name, lambda *a, **k: None)
    log = []

    async def fake(cfg, conn, text, route, emit, speak, prepare=None, **kw):
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


def confirm_runner(log):
    async def fake(cfg, conn, text, route, emit, speak, **kw):
        try:
            ok = await kw["confirm"]("Close Notepad, sir?")
            log.append(ok)
        except asyncio.CancelledError:
            log.append("cancelled")
            await emit({"type": "turn_end"})
            raise
        await emit({"type": "turn_end"})
    return fake


def test_confirm_resolved_by_button(cfg, conn):
    log = []
    with TestClient(create_app(cfg, conn, turn_runner=confirm_runner(log))) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "text_input", "text": "close notepad"})
        c = recv_until(ws, lambda m: m["type"] == "confirm")[-1]
        assert c["text"] == "Close Notepad, sir?"
        ws.send_json({"type": "confirm_reply", "id": c["id"], "ok": True})
        seen = recv_until(ws, lambda m: m["type"] == "turn_end")
        ctx.__exit__(None, None, None)
    assert log == [True] and {"type": "confirm_done", "id": c["id"], "ok": True} in seen


class FakeVoice:
    available = True

    def __init__(self):
        self.events, self.confirm_id = [], None

    async def speak(self, text):
        pass

    def prepare(self, text):
        pass

    def event(self, name):
        self.events.append(name)


def test_confirm_resolved_by_voice_text(cfg, conn):
    log = []
    voice = FakeVoice()
    app = create_app(cfg, conn, voice=voice, turn_runner=confirm_runner(log))
    with TestClient(app) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "text_input", "text": "close notepad"})
        c = recv_until(ws, lambda m: m["type"] == "confirm")[-1]
        assert voice.confirm_id == c["id"] and "confirm_listen" in voice.events
        app.state.hub.from_thread("confirm_heard", {"text": "Yes, go ahead.", "id": c["id"]})
        recv_until(ws, lambda m: m["type"] == "turn_end")
        ctx.__exit__(None, None, None)
    assert log == [True]


def test_confirm_times_out_no(cfg, conn, monkeypatch):
    from jarvis import app as app_mod
    monkeypatch.setattr(app_mod, "BUTTON_WAIT_S", 0.1)
    log = []
    with TestClient(create_app(cfg, conn, turn_runner=confirm_runner(log))) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "text_input", "text": "close notepad"})
        recv_until(ws, lambda m: m["type"] == "turn_end")
        ctx.__exit__(None, None, None)
    assert log == [False]


def test_cancel_resolves_pending_confirm_false(cfg, conn):
    log = []
    app = create_app(cfg, conn, turn_runner=confirm_runner(log))
    with TestClient(app) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "text_input", "text": "close notepad"})
        recv_until(ws, lambda m: m["type"] == "confirm")
        ws.send_json({"type": "cancel"})
        recv_until(ws, lambda m: m["type"] == "turn_end")
        ctx.__exit__(None, None, None)
    assert log == ["cancelled"] and app.state.hub.pending == {}


def test_undo_message_undoes_action(cfg, conn):
    from jarvis import db
    mid = db.add_memory(conn, "x")
    aid = db.log_action(conn, "remember", {"text": "x"}, "ok", "done",
                        {"kind": "delete_memory", "id": mid}, None)
    with TestClient(create_app(cfg, conn)) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "undo", "id": aid})
        m = recv_until(ws, lambda m: m["type"] == "action")[-1]
        ws.send_json({"type": "undo", "id": aid})
        b = recv_until(ws, lambda m: m["type"] == "banner")[-1]
        ctx.__exit__(None, None, None)
    assert m["status"] == "undone" and db.memories(conn) == [] and "undo" in b["text"].lower()


def test_actions_sent_on_connect(cfg, conn):
    from jarvis import db
    db.log_action(conn, "remember", {"text": "x"}, "ok", "done", None, None)
    with TestClient(create_app(cfg, conn)) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json(), ws.receive_json()
            a = ws.receive_json()
    assert a["type"] == "actions" and a["actions"][0]["tool"] == "remember"


def test_late_voice_answer_does_not_approve_next_question(cfg, conn, monkeypatch):
    from jarvis import app as app_mod
    monkeypatch.setattr(app_mod, "VOICE_WAIT_S", 0.5)
    log = []

    async def two_questions(cfg, conn, text, route, emit, speak, **kw):
        log.append(await kw["confirm"]("Close Notepad, sir?"))
        log.append(await kw["confirm"]("Close Chrome, sir?"))
        await emit({"type": "turn_end"})
    voice = FakeVoice()
    app = create_app(cfg, conn, voice=voice, turn_runner=two_questions)
    with TestClient(app) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "text_input", "text": "close both"})
        c1 = recv_until(ws, lambda m: m["type"] == "confirm")[-1]
        ws.send_json({"type": "confirm_reply", "id": c1["id"], "ok": True})
        recv_until(ws, lambda m: m["type"] == "confirm")
        # the "yes" spoken to the first question arrives late, after the button answered it
        app.state.hub.from_thread("confirm_heard", {"text": "yes", "id": c1["id"]})
        recv_until(ws, lambda m: m["type"] == "turn_end")
        ctx.__exit__(None, None, None)
    assert log == [True, False]


def test_new_chat_files_the_old_one_and_open_chat_resumes_it(cfg, conn):
    from jarvis import db
    db.add_message(conn, "user", "first question")
    db.add_message(conn, "assistant", "first answer")
    with TestClient(create_app(cfg, conn)) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "new_chat"})
        hist = recv_until(ws, lambda m: m["type"] == "history")[-1]
        assert hist["messages"] == []
        chats = recv_until(ws, lambda m: m["type"] == "chats")[-1]
        old = [c for c in chats["chats"] if c["title"] == "first question"][0]
        assert old["count"] == 2 and chats["current"] != old["id"]
        ws.send_json({"type": "new_chat"})   # the new chat is still empty: reused, not filed
        assert recv_until(ws, lambda m: m["type"] == "chats")[-1]["current"] == chats["current"]
        ws.send_json({"type": "open_chat", "id": old["id"]})
        hist = recv_until(ws, lambda m: m["type"] == "history")[-1]
        assert [m["content"] for m in hist["messages"]] == ["first question", "first answer"]
        ctx.__exit__(None, None, None)


def test_mute_without_a_mic_reports_back(cfg, conn):
    with TestClient(create_app(cfg, conn)) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "mute", "on": True})
        assert recv_until(ws, lambda m: m["type"] == "mic")[-1]["available"] is False
        ctx.__exit__(None, None, None)


def test_save_upload_never_overwrites_and_strips_paths(tmp_path):
    import base64

    from jarvis.app import save_upload
    data = base64.b64encode(b"hello").decode()
    a = save_upload(r"..\..\evil.txt", data, tmp_path)
    b = save_upload("evil.txt", data, tmp_path)
    assert a["name"] == "evil.txt" and b["name"] == "evil (1).txt"
    assert (tmp_path / "evil (1).txt").read_bytes() == b"hello"
    assert save_upload("x.txt", "not base64!", tmp_path)["type"] == "banner"


def test_save_screenshot_attaches_a_png_and_reports_failures(tmp_path):
    from PIL import Image

    from jarvis.app import save_screenshot
    r = save_screenshot(tmp_path, grab=lambda: Image.new("RGB", (4, 3)))
    assert r["type"] == "uploaded" and r["name"].startswith("Screenshot ") and r["name"].endswith(".png")
    assert Image.open(r["path"]).size == (4, 3)

    def fail():
        raise OSError("no display")
    assert save_screenshot(tmp_path, grab=fail) == {"type": "banner",
                                                    "text": "Couldn't take a screenshot: no display"}


def test_screenshot_hides_only_the_jarvis_window_and_puts_it_back(monkeypatch):
    from jarvis import app as app_mod
    from jarvis.tools import windows
    log = []
    monkeypatch.setattr(windows, "list_top", lambda: [
        {"hwnd": 1, "title": "Jarvis", "exe": r"C:\\Program Files\\Edge\\msedge.exe"},
        {"hwnd": 2, "title": "Jarvis notes.txt - Notepad", "exe": "notepad.exe"},
        {"hwnd": 3, "title": "Jarvis", "exe": "notepad.exe"},
        {"hwnd": 4, "title": "Jarvis", "exe": "msedge"}])   # list_top reports bare names too
    monkeypatch.setattr(windows, "_show", lambda h, cmd: log.append(("show", h, cmd)))
    monkeypatch.setattr(windows, "_bring", lambda h: log.append(("bring", h)))

    def grab():
        log.append("grab")
        return "image"
    assert app_mod.grab_without_jarvis(grab, wait=0) == "image"
    assert log == [("show", 1, app_mod.SW_HIDE), ("show", 4, app_mod.SW_HIDE), "grab",
                   ("show", 1, app_mod.SW_SHOW), ("bring", 1), ("show", 4, app_mod.SW_SHOW), ("bring", 4)]


def test_screenshot_puts_the_window_back_even_if_the_grab_fails(monkeypatch):
    from jarvis import app as app_mod
    from jarvis.tools import windows
    log = []
    monkeypatch.setattr(windows, "list_top", lambda: [{"hwnd": 1, "title": "Jarvis", "exe": "msedge.exe"}])
    monkeypatch.setattr(windows, "_show", lambda h, cmd: log.append(cmd))
    monkeypatch.setattr(windows, "_bring", lambda h: log.append("bring"))

    def grab():
        raise OSError("no display")
    r = app_mod.save_screenshot(grab=lambda: app_mod.grab_without_jarvis(grab, wait=0))
    assert r["type"] == "banner" and log == [app_mod.SW_HIDE, app_mod.SW_SHOW, "bring"]


def test_open_item_unknown_id_says_moved_or_deleted(cfg, conn):
    with TestClient(create_app(cfg, conn)) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "open_item", "kind": "artifact", "id": 999})
        seen = recv_until(ws, lambda m: m["type"] == "banner")
        ctx.__exit__(None, None, None)
    assert "moved or deleted" in seen[-1]["text"]


def test_open_item_opens_a_vault_file_by_id_not_by_path(cfg, conn, tmp_path, monkeypatch):
    f = tmp_path / "a.txt"
    f.write_text("x")
    conn.execute("INSERT INTO vault(ts, kind, title, text, path, url, sha) "
                 "VALUES('t','file','a','',?,'','s')", (str(f),))
    opened = []
    monkeypatch.setattr("os.startfile", opened.append, raising=False)
    with TestClient(create_app(cfg, conn)) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "open_item", "kind": "vault", "id": 1, "path": "C:/evil.exe"})
        ws.send_json({"type": "full_view"})   # the banner it answers with proves the open ran first
        recv_until(ws, lambda m: m["type"] == "banner")
        ctx.__exit__(None, None, None)
    assert opened == [str(f)]


def test_keep_text_from_the_island_lands_in_the_vault(cfg, conn):
    with TestClient(create_app(cfg, conn)) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "keep", "text": "locker 214"})
        seen = recv_until(ws, lambda m: m["type"] == "vault")
        ctx.__exit__(None, None, None)
    assert seen[-1]["items"][0]["title"] == "locker 214"


def test_remove_vault_item_and_full_view_default(cfg, conn):
    conn.execute("INSERT INTO vault(ts, kind, title, text, path, url, sha) "
                 "VALUES('t','note','n','n','','','s')")
    with TestClient(create_app(cfg, conn)) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "remove_vault", "id": 1})
        seen = recv_until(ws, lambda m: m["type"] == "vault")
        ws.send_json({"type": "full_view"})
        banner = recv_until(ws, lambda m: m["type"] == "banner")[-1]
        ctx.__exit__(None, None, None)
    assert seen[-1]["items"] == [] and "isn't available" in banner["text"]


def test_island_page_served(cfg, conn, tmp_path, monkeypatch):
    (tmp_path / "island.html").write_text("<html>island</html>")
    monkeypatch.setattr("jarvis.app.UI", tmp_path)
    with TestClient(create_app(cfg, conn)) as client:
        r = client.get("/island")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")


def test_fullscreen_state_reaches_the_island(cfg, conn, monkeypatch):
    monkeypatch.setattr("jarvis.fullscreen._query", lambda: 3)
    with TestClient(create_app(cfg, conn)) as client, client.websocket_connect("/ws") as ws:
        # sent when the watcher notices, or on connect if it noticed first
        seen = recv_until(ws, lambda m: m["type"] == "fullscreen")
    assert seen[-1] == {"type": "fullscreen", "on": True}


def test_sync_full_view_runs_off_the_event_loop(cfg, conn):
    import asyncio
    on_loop = []

    def open_view():
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)

    app = create_app(cfg, conn)
    app.state.hub.open_full_view = open_view
    with TestClient(app) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "full_view"})
        ws.send_json({"type": "open_item", "kind": "artifact", "id": 1})
        recv_until(ws, lambda m: m["type"] == "banner")   # answered after full_view was handled
        ctx.__exit__(None, None, None)
    assert on_loop == [False]


def test_open_item_for_an_artifact_whose_file_is_gone(cfg, conn, tmp_path):
    from jarvis import db
    aid = db.log_action(conn, "create_file", {"path": str(tmp_path / "gone.md")}, "Created",
                        "done", None, None)
    with TestClient(create_app(cfg, conn)) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "open_item", "kind": "artifact", "id": aid})
        seen = recv_until(ws, lambda m: m["type"] == "banner")
        ctx.__exit__(None, None, None)
    assert "moved or deleted" in seen[-1]["text"]


def test_open_item_ignores_non_http_links(cfg, conn, monkeypatch):
    conn.execute("INSERT INTO vault(ts, kind, title, text, path, url, sha) "
                 "VALUES('t','link','l','','','file:///C:/Windows/notepad.exe','s')")
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    with TestClient(create_app(cfg, conn)) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "open_item", "kind": "vault", "id": 1})
        recv_until(ws, lambda m: m["type"] == "banner")
        ctx.__exit__(None, None, None)
    assert opened == []


def test_keep_with_data_saves_the_file_and_prefers_it_over_text(cfg, conn, tmp_path, monkeypatch):
    import base64
    monkeypatch.setattr("jarvis.app.UPLOADS", tmp_path)
    cfg.file_roots, cfg.app_dir = [tmp_path], tmp_path / "app"
    with TestClient(create_app(cfg, conn)) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "keep", "name": "n.txt", "text": "ignored",
                      "data": base64.b64encode(b"hello").decode()})
        seen = recv_until(ws, lambda m: m["type"] == "vault")
        ctx.__exit__(None, None, None)
    [item] = seen[-1]["items"]
    assert item["kind"] == "file" and item["title"] == "n.txt"
    assert (tmp_path / "n.txt").read_bytes() == b"hello"


def test_keep_with_nothing_says_so(cfg, conn):
    with TestClient(create_app(cfg, conn)) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "keep"})
        seen = recv_until(ws, lambda m: m["type"] == "banner")
        ctx.__exit__(None, None, None)
    assert seen[-1]["text"] == "Nothing to keep"


def test_island_mic_button_is_push_to_talk(cfg, conn):
    voice = FakeVoice()
    with TestClient(create_app(cfg, conn, voice=voice)) as client:
        assert client.get("/island").status_code == 200
        ctx, ws = connect(client)
        ws.send_json({"type": "ptt", "down": True})
        ws.send_json({"type": "ptt", "down": False})
        ws.send_json({"type": "mute", "on": False})   # replies, so the ptts were handled
        recv_until(ws, lambda m: m["type"] == "mic")
        ctx.__exit__(None, None, None)
    assert voice.events == ["hotkey_down", "hotkey_up"]


def test_island_ptt_without_voice_is_ignored(cfg, conn):
    app = create_app(cfg, conn)
    with TestClient(app) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "ptt", "down": True})
        ws.send_json({"type": "mute", "on": False})
        mic = recv_until(ws, lambda m: m["type"] == "mic")[-1]
        ws.send_json({"type": "mute", "on": True})   # the socket still answers after the ptt
        assert recv_until(ws, lambda m: m["type"] == "mic")[-1]["type"] == "mic"
        ctx.__exit__(None, None, None)
    assert app.state.hub.voice is None and mic["available"] is False


def test_transcript_turn_is_started_untyped(cfg, conn):
    seen = []

    async def fake(cfg, conn, text, route, emit, speak, **kw):
        seen.append((text, kw["typed"]))
        await emit({"type": "turn_end"})

    app = create_app(cfg, conn, turn_runner=fake)
    with TestClient(app) as client:
        ctx, ws = connect(client)
        hub = app.state.hub
        hub.from_thread("transcript", {"text": "what time is it"})
        ws.send_json({"type": "text_input", "text": "typed"})
        recv_until(ws, lambda m: m["type"] == "turn_end" and len(seen) == 2)
        ctx.__exit__(None, None, None)
    assert seen == [("what time is it", False), ("typed", True)]


def test_keep_with_data_that_is_a_duplicate_deletes_its_own_copy(cfg, conn, tmp_path, monkeypatch):
    import base64
    monkeypatch.setattr("jarvis.app.UPLOADS", tmp_path)
    cfg.file_roots, cfg.app_dir = [tmp_path], tmp_path / "app"
    data = base64.b64encode(b"hello").decode()
    with TestClient(create_app(cfg, conn)) as client:
        ctx, ws = connect(client)
        ws.send_json({"type": "keep", "name": "n.txt", "data": data})
        recv_until(ws, lambda m: m["type"] == "vault")
        ws.send_json({"type": "keep", "name": "n.txt", "data": data})   # same bytes again
        recv_until(ws, lambda m: m["type"] == "action" and m["status"] == "done")
        ctx.__exit__(None, None, None)
    assert sorted(p.name for p in tmp_path.glob("n*.txt")) == ["n.txt"]
