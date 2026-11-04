import asyncio

from fastapi.testclient import TestClient

from jarvis import desk
from jarvis.app import Hub, create_app
from tests.test_app import recv_until


class Sock:
    def __init__(self):
        self.got = []

    async def send_json(self, m):
        self.got.append(m)


def hub_with_sock(cfg, conn):
    h, s = Hub(cfg, conn, None, None), Sock()
    h.sockets.add(s)
    return h, s


async def test_focus_starts_stops_and_ends_with_a_note(cfg, conn, monkeypatch):
    h, s = hub_with_sock(cfg, conn)
    await h.set_focus(25)
    assert h.focus["minutes"] == 25 and h.focus["until"] > 0
    await h.set_focus(0)
    assert h.focus == {"type": "focus", "until": 0, "minutes": 0}
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: real_sleep(0))
    await h.set_focus(1)
    await h.focus_task
    assert h.focus["minutes"] == 0 and s.got[-1]["type"] == "notify" and "1 minutes" in s.got[-1]["text"]


async def test_media_change_broadcasts_once_with_cover(cfg, conn, monkeypatch):
    h, s = hub_with_sock(cfg, conn)
    songs = [{"title": "Golden Hour", "artist": "Nova Lane", "app": "Spotify", "playing": True}] * 3

    async def media(thumb=False):
        m = dict(songs[0])
        if thumb:
            m["thumb"] = "data:image/png;base64,AA"
        return m
    monkeypatch.setattr(desk, "media", media)
    calls = 0
    real_sleep = asyncio.sleep

    async def tick(_):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise asyncio.CancelledError
        await real_sleep(0)
    monkeypatch.setattr(asyncio, "sleep", tick)
    try:
        await h.watch_media()
    except asyncio.CancelledError:
        pass
    media_msgs = [m for m in s.got if m["type"] == "media"]
    assert len(media_msgs) == 1 and media_msgs[0]["thumb"].startswith("data:image/png")
    assert media_msgs[0]["title"] == "Golden Hour" and media_msgs[0]["playing"] is True


async def test_media_control_says_when_nothing_plays(cfg, conn, monkeypatch):
    h, s = hub_with_sock(cfg, conn)

    async def none(action):
        return False
    monkeypatch.setattr(desk, "media_control", none)
    await h.media_control("toggle")
    assert s.got == [{"type": "banner", "text": "Nothing is playing"}]


def test_new_page_gets_focus_media_and_claude_progress(cfg, conn):
    app = create_app(cfg, conn)
    hub = app.state.hub
    hub.claude_live = {"type": "claude_live", "step": "edit", "file": "a.py", "project": "p"}
    with TestClient(app) as client:
        cm = client.websocket_connect("/ws")
        ws = cm.__enter__()
        assert recv_until(ws, lambda m: m["type"] == "claude_live")[-1]["file"] == "a.py"
        ws.send_json({"type": "focus", "minutes": 25})
        assert recv_until(ws, lambda m: m["type"] == "focus" and m["minutes"] == 25)[-1]["until"] > 0
        cm.__exit__(None, None, None)


async def test_claude_progress_is_remembered(cfg, conn):
    h, _ = hub_with_sock(cfg, conn)
    await h.broadcast({"type": "claude_live", "step": "read", "file": "x"})
    assert h.claude_live["step"] == "read"


async def test_winrt_calls_run_off_the_event_loop_thread():
    # comtypes makes the main thread a single-threaded COM apartment; WinRT awaited there can hang
    import threading

    @desk._off_thread
    async def where(x, y=0):
        return threading.get_ident(), x + y

    tid, total = await where(1, y=2)
    assert tid != threading.get_ident() and total == 3
