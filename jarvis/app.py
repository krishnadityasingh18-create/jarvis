import asyncio
import base64
import inspect
import json
import os
import time as clock_time
import webbrowser
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from jarvis import artifacts, brain, db, fullscreen, remote, tools
from jarvis.tools import outbound, vault
from jarvis.tools.apps import _runs_code

UI = Path(__file__).parent / "ui"
ALLOWED_ORIGINS = {"http://127.0.0.1:8765", "http://localhost:8765"}
TUNNEL_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "forwarded", "x-real-ip",
                  "ngrok-trace-id")


def _tunnelled(headers) -> bool:
    return any(h in headers for h in TUNNEL_HEADERS)


UPLOADS = Path.home() / "Downloads" / "JARVIS uploads"   # inside files.roots: JARVIS can read them
MAX_UPLOAD = 10 * 1024 * 1024   # base64 over the socket; uvicorn caps a message at 16 MB

VAULT_TOOLS = {"keep", "remove_from_vault"}
DAY_TOOLS = {"remind", "cancel_reminder", "add_task", "complete_task", "add_class", "remove_class"}
GLANCE_EVERY_S = 15 * 60

VOICE_WAIT_S = 20     # the voice loop listens 8 s and always reports back; this is a backstop
BUTTON_WAIT_S = 60    # no mic: wait this long for a click


async def _no_speak(text: str) -> None:
    pass


class Hub:
    """Owns the sockets and the one running turn. All methods run on the server loop."""

    def __init__(self, cfg, conn, voice, turn_runner):
        self.cfg, self.conn, self.voice, self.turn_runner = cfg, conn, voice, turn_runner
        self.sockets: set[WebSocket] = set()
        self.turn: asyncio.Task | None = None
        self.route = cfg.default_route
        self.loop: asyncio.AbstractEventLoop | None = None
        self.pending: dict[str, asyncio.Future] = {}   # open confirmation questions
        self.tasks: set[asyncio.Task] = set()          # island requests that may wait on a yes
        self.fullscreen = False
        self.open_full_view = self._no_full_view  # __main__ replaces it with the Edge HUD

    async def _no_full_view(self) -> None:
        await self.broadcast({"type": "banner", "text": "Full view isn't available"})

    def vault(self) -> dict:
        return {"type": "vault", "items": vault.items(self.conn)}

    def artifacts(self) -> dict:
        return {"type": "artifacts", "items": artifacts.items(self.conn)}

    def glance(self) -> dict:
        return {"type": "glance", "items": artifacts.glance(self.cfg, self.conn)}

    def spawn(self, coro) -> None:
        """Run an island request beside the socket loop (a big keep asks, and the answer
        arrives on that same socket)."""
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    async def desk_run(self, name: str, **args) -> str:
        ctx = tools.Ctx(self.cfg, self.conn, self.confirm, self.broadcast)
        return await tools.run(ctx, {"id": "island", "name": name, "arguments": json.dumps(args)})

    async def keep(self, m: dict) -> None:
        if m.get("data"):   # a dropped file wins over text
            up = await asyncio.to_thread(save_upload, str(m.get("name", "")), str(m["data"]))
            if up["type"] != "uploaded":
                await self.broadcast(up)
                return
            await self.desk_run("keep", path=up["path"])
        elif m.get("text"):
            await self.desk_run("keep", text=str(m["text"]))
        else:
            await self.broadcast({"type": "banner", "text": "Nothing to keep"})

    async def open_item(self, kind: str, item_id) -> None:
        """Open a vault or artifact item by id; the path always comes from our own records."""
        if kind == "vault":
            it = next((i for i in vault.items(self.conn) if i["id"] == item_id), None)
        elif kind == "artifact":
            it = await asyncio.to_thread(artifacts.get, self.conn, item_id)
        else:
            it = None
        path = (it or {}).get("path") or ""
        if it and it.get("url"):
            if urlparse(it["url"]).scheme in ("http", "https"):
                await asyncio.to_thread(webbrowser.open, it["url"])
                return
        elif path and Path(path).is_absolute() and Path(path).exists():
            if _runs_code(Path(path).name):
                await self.broadcast({"type": "banner", "text": "I don't open programs or scripts"})
            else:
                await asyncio.to_thread(os.startfile, path)
            return
        await self.broadcast({"type": "banner", "text": "That item was moved or deleted"})

    async def send_glance(self, sock: WebSocket | None = None) -> None:
        """The glance may wait on the weather: always run it beside the caller."""
        msg = await asyncio.to_thread(self.glance)
        try:
            await (sock.send_json(msg) if sock else self.broadcast(msg))
        except Exception:
            self.sockets.discard(sock)   # that window went away

    async def send_island_data(self, sock: WebSocket) -> None:
        await sock.send_json(self.vault())
        await sock.send_json(await asyncio.to_thread(self.artifacts))
        self.spawn(self.send_glance(sock))
        if self.fullscreen:
            await sock.send_json({"type": "fullscreen", "on": True})

    def usage(self) -> dict:
        return {"type": "usage", "spent_inr": round(db.month_spend_inr(self.conn), 2),
                "cap_inr": self.cfg.monthly_inr, "requests_today": db.requests_today(self.conn)}

    def chats(self) -> dict:
        return {"type": "chats", "chats": db.chats(self.conn), "current": db.current_chat(self.conn)}

    def history(self) -> dict:
        return {"type": "history", "messages": db.recent_messages(self.conn, 20),
                "route": self.route}

    def mic(self) -> dict:
        return {"type": "mic", "muted": bool(getattr(self.voice, "muted", False)),
                "available": bool(self.voice and self.voice.available)}

    async def switch_chat(self, chat: int | None) -> None:
        """chat None: start a new one. Stops the running turn first."""
        await self._stop_turn()
        if chat is None:
            db.new_chat(self.conn)
        else:
            db.open_chat(self.conn, chat)
        await self.broadcast(self.history())
        await self.broadcast(self.chats())

    async def set_muted(self, muted: bool) -> None:
        if self.voice:
            from jarvis.voice import State
            self.voice.muted = muted
            if muted and self.voice.fsm.state in (State.LISTENING, State.CONFIRMING):
                self.voice.event("cancel")
        await self.broadcast(self.mic())

    async def broadcast(self, msg: dict) -> None:
        if self.voice:  # keep the voice state machine in step with the turn
            if msg == {"type": "state", "state": "speaking"}:
                self.voice.event("speak_start")
            elif msg["type"] == "turn_end":
                self.voice.event("turn_done")
        for ws in list(self.sockets):
            try:
                await ws.send_json(msg)
            except Exception:
                self.sockets.discard(ws)
        if msg["type"] == "turn_end":
            await self.broadcast(self.usage())
            await self.broadcast(self.chats())
            await self.broadcast(await asyncio.to_thread(self.artifacts))
        elif msg["type"] == "action":   # also fires for an undo of the same tool
            if msg["tool"] in VAULT_TOOLS:
                await self.broadcast(self.vault())
            elif msg["tool"] in DAY_TOOLS:
                self.spawn(self.send_glance())

    async def confirm(self, text: str) -> bool:
        """Ask the user; resolved by a button, a spoken yes/no, a timeout or a cancel (no)."""
        cid = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self.pending[cid] = fut
        voice_up = bool(self.voice and self.voice.available)
        wait = VOICE_WAIT_S if voice_up else BUTTON_WAIT_S
        ok = False
        try:
            await self.broadcast({"type": "confirm", "id": cid, "text": text, "seconds": wait})
            if voice_up:
                await self.broadcast({"type": "state", "state": "speaking"})
                await self.voice.speak(text)
                if not fut.done():
                    self.voice.confirm_id = cid   # a late spoken answer can't hit the next question
                    self.voice.event("confirm_listen")
            ok = await asyncio.wait_for(asyncio.shield(fut), wait)
        except TimeoutError:
            ok = False
        finally:
            self.pending.pop(cid, None)
            await self.broadcast({"type": "confirm_done", "id": cid, "ok": ok})
        return ok

    def _answer(self, ok: bool, cid: str | None = None) -> None:
        """Resolve the given question, or the oldest open one."""
        for k, fut in list(self.pending.items()):
            if (cid is None or k == cid) and not fut.done():
                fut.set_result(ok)
                return

    async def undo(self, action_id: int) -> None:
        try:
            await asyncio.to_thread(tools.undo, self.conn, action_id)
            await self.broadcast({"type": "action", **db.get_action(self.conn, action_id)})
        except Exception as e:
            await self.broadcast({"type": "banner", "text": f"Couldn't undo: {e}"})

    BRIEFING_PROMPT = "Give me my morning briefing."
    BRIEFING_UNTIL = time(12, 0)   # a "morning" briefing never starts in the afternoon
    READY_TIMEOUT_S = 120

    def ready(self, elapsed: float) -> bool:
        """Only act once JARVIS can be heard and seen: speech loaded (or failed) and the
        window connected. After a couple of minutes, stop waiting for the window."""
        voice_ok = self.voice is None or getattr(self.voice, "loaded", True)
        return voice_ok and (bool(self.sockets) or elapsed >= self.READY_TIMEOUT_S)

    async def tick(self, now: datetime) -> None:
        """Every 30 s: speak due reminders, and start the morning briefing once a day.
        These are the only things JARVIS does unasked (the user set them up)."""
        if self.turn is not None and not self.turn.done() and not self.turn.cancelling():
            return   # never talk over a reply; try again next tick
        if self.voice is not None:
            from jarvis.voice import State
            if self.voice.fsm.state is not State.IDLE:
                return   # the user is talking, or JARVIS is: wait
        await asyncio.to_thread(outbound.settle_calls, self.cfg, self.conn)
        due = self.conn.execute("SELECT id, at, text, call FROM reminders WHERE done=0 AND "
                                "at <= ? ORDER BY at",
                                (now.isoformat(timespec="minutes"),)).fetchall()
        for rid, at, text, phone in due:
            self.conn.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,))
            late = datetime.fromisoformat(at) < now - timedelta(minutes=5)
            await self.broadcast({"type": "banner",
                                  "text": f"Reminder{' (late)' if late else ''}: {text}"})
            if self.voice:
                await self.voice.speak(f"{'A late reminder' if late else 'Reminder'}, sir: {text}")
            if phone and not late:   # a late one was missed while JARVIS was off: no call
                try:
                    await asyncio.to_thread(outbound.ring_me, self.cfg, self.conn,
                                            f"A reminder: {text}")
                except Exception as e:
                    await self.broadcast({"type": "banner",
                                          "text": f"Couldn't phone you about it: {e}"})
        if due or not self.cfg.briefing_enabled or \
                not self.cfg.briefing_time <= now.time() < self.BRIEFING_UNTIL:
            return
        key = f"briefed:{now.date().isoformat()}"
        if not self.conn.execute("SELECT 1 FROM kv WHERE key=?", (key,)).fetchone():
            self.conn.execute("INSERT INTO kv(key, value) VALUES(?,?)", (key, now.isoformat()))
            await self.start_turn(self.BRIEFING_PROMPT, typed=True)   # voice: idle → thinking

    async def clock(self) -> None:
        start = last_glance = clock_time.monotonic()
        while True:
            try:
                if self.ready(clock_time.monotonic() - start):
                    await self.tick(datetime.now())
                if clock_time.monotonic() - last_glance >= GLANCE_EVERY_S:
                    last_glance = clock_time.monotonic()
                    self.spawn(self.send_glance())
            except Exception as e:   # a bad row must not stop reminders for good
                await self.broadcast({"type": "banner", "text": f"Scheduler error: {e}"})
            await asyncio.sleep(30)

    async def watch_fullscreen(self) -> None:
        """Tell the island when a game, video or presentation takes the screen."""
        while True:
            on = await asyncio.to_thread(fullscreen.state)
            if on != self.fullscreen:
                self.fullscreen = on
                await self.broadcast({"type": "fullscreen", "on": on})
            await asyncio.sleep(2)

    async def _stop_turn(self) -> None:
        for fut in self.pending.values():   # an open question counts as "no"
            if not fut.done():
                fut.set_result(False)
        t, self.turn = self.turn, None
        if t and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    async def start_turn(self, text: str, typed: bool = True) -> None:
        text = text.strip()
        if not text:
            return
        await self._stop_turn()  # old turn's turn_end resets the voice FSM to idle first
        if typed and self.voice:
            self.voice.event("text_input")
        await self.broadcast({"type": "user_text", "text": text})
        args = (self.cfg, self.conn, text, brain.pick_route(text, self.route), self.broadcast)
        if self.voice:
            coro = self.turn_runner(*args, self.voice.speak, prepare=self.voice.prepare,
                                    confirm=self.confirm)
        else:
            coro = self.turn_runner(*args, _no_speak, confirm=self.confirm)
        self.turn = asyncio.create_task(coro)

    async def cancel(self) -> None:
        await self._stop_turn()
        if self.voice:
            self.voice.event("cancel")

    def from_thread(self, name: str, payload: dict) -> None:
        """VoiceLoop.on_event: called from the voice thread."""
        if self.loop is None:
            return
        if name == "transcript":
            coro = lambda: self.start_turn(payload["text"], typed=False)  # noqa: E731
        elif name == "cancel_turn":
            coro = self._stop_turn
        elif name == "confirm_heard":
            if payload.get("id"):
                ok = tools.parse_yes_no(payload["text"])
                self.loop.call_soon_threadsafe(self._answer, ok, payload["id"])
            return
        else:  # state, banner
            coro = lambda: self.broadcast({"type": name, **payload})  # noqa: E731
        self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(coro()))


WINDOW_TITLE = "Jarvis"   # the desk window's title (the page's <title>)
SW_HIDE, SW_SHOW = 0, 5
REPAINT_S = .25           # after hiding, give the windows underneath a moment to repaint


def grab_without_jarvis(grab=None, wait=REPAINT_S):
    """The screen as the user sees it with JARVIS's own window out of the way: hidden
    (instant; minimising animates and can be caught mid-fade), captured, then put back."""
    import time

    from jarvis.tools import windows
    if grab is None:
        from PIL import ImageGrab
        grab = lambda: ImageGrab.grab(all_screens=True)  # noqa: E731
    mine = [w["hwnd"] for w in windows.list_top()
            if w["title"] == WINDOW_TITLE and Path(w["exe"]).stem.lower() == "msedge"]
    for h in mine:
        windows._show(h, SW_HIDE)
    try:
        if mine:
            time.sleep(wait)
        return grab()
    finally:
        for h in mine:
            windows._show(h, SW_SHOW)
            windows._bring(h)


def save_screenshot(folder: Path | None = None, grab=None) -> dict:
    """The UI's "Take a screenshot": every screen without JARVIS's window, saved next to
    the uploads, then attached."""
    grab = grab or grab_without_jarvis
    folder = folder or UPLOADS
    try:
        img = grab()
    except Exception as e:
        return {"type": "banner", "text": f"Couldn't take a screenshot: {e}"}
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"Screenshot {datetime.now():%Y-%m-%d %H%M%S}.png"
    img.save(path)
    return {"type": "uploaded", "name": path.name, "path": str(path)}


def save_upload(name: str, data_b64: str, folder: Path | None = None) -> dict:
    """A file attached in the UI: saved (never overwriting) where JARVIS's file tools reach."""
    folder = folder or UPLOADS
    name = Path(name.replace("\\", "/")).name.strip() or "upload"
    try:
        data = base64.b64decode(data_b64, validate=True)
    except ValueError:
        return {"type": "banner", "text": f"Couldn't attach {name}: bad data"}
    if len(data) > MAX_UPLOAD:
        return {"type": "banner", "text": f"Couldn't attach {name}: over 10 MB"}
    folder.mkdir(parents=True, exist_ok=True)
    path, n = folder / name, 1
    while path.exists():
        path, n = folder / f"{Path(name).stem} ({n}){Path(name).suffix}", n + 1
    path.write_bytes(data)
    return {"type": "uploaded", "name": path.name, "path": str(path)}


def create_app(cfg, conn, voice=None, turn_runner=brain.run_turn) -> FastAPI:
    hub = Hub(cfg, conn, voice, turn_runner)

    @asynccontextmanager
    async def lifespan(app):
        hub.loop = asyncio.get_running_loop()
        bg = [asyncio.create_task(hub.clock()), asyncio.create_task(hub.watch_fullscreen())]
        yield
        for t in bg:
            t.cancel()

    app = FastAPI(lifespan=lifespan)
    app.state.hub = hub
    remote.routes(app, hub)   # the away channel (token-protected); the UI stays below

    @app.middleware("http")
    async def desk_only(request, call_next):
        # Through a tunnel everything looks local, and a script can fake any Origin; but
        # tunnels always add a forwarding header the outside can't remove. Such requests may
        # only reach the token-protected away-channel API, never the desk UI.
        if _tunnelled(request.headers) and request.url.path not in remote.PATHS:
            return JSONResponse({"detail": "the desk is local-only"}, status_code=403)
        response = await call_next(request)
        if request.url.path in ("/", "/island") or request.url.path.startswith("/ui/"):
            response.headers["Cache-Control"] = "no-cache"   # a UI update shows on the next load
        return response

    @app.get("/")
    async def index():
        return FileResponse(UI / "index.html")

    @app.get("/island")
    async def island():
        return FileResponse(UI / "island.html")

    app.mount("/ui", StaticFiles(directory=UI), name="ui")

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        # WebSockets bypass CORS: refuse pages from other sites (browsers always send Origin).
        origin = sock.headers.get("origin")
        if (origin and origin not in ALLOWED_ORIGINS) or _tunnelled(sock.headers):
            await sock.close(code=1008)
            return
        await sock.accept()
        hub.loop = asyncio.get_running_loop()
        hub.sockets.add(sock)
        await sock.send_json(hub.history())
        await sock.send_json(hub.usage())
        await sock.send_json({"type": "actions", "actions": db.recent_actions(conn, 30)})
        await sock.send_json(hub.chats())
        await sock.send_json(hub.mic())
        await hub.send_island_data(sock)
        try:
            while True:
                m = await sock.receive_json()
                if m.get("type") == "text_input":
                    await hub.start_turn(str(m.get("text", "")))
                elif m.get("type") == "cancel":
                    await hub.cancel()
                elif m.get("type") == "set_route" and m.get("route") in cfg.routes:
                    hub.route = m["route"]
                elif m.get("type") == "confirm_reply":
                    hub._answer(bool(m.get("ok")), str(m.get("id")))
                    if hub.voice:
                        hub.voice.event("confirm_done")
                elif m.get("type") == "undo" and isinstance(m.get("id"), int):
                    await hub.undo(m["id"])
                elif m.get("type") == "new_chat":
                    await hub.switch_chat(None)
                elif m.get("type") == "open_chat" and isinstance(m.get("id"), int):
                    await hub.switch_chat(m["id"])
                elif m.get("type") == "mute":
                    await hub.set_muted(bool(m.get("on")))
                elif m.get("type") == "screenshot":
                    await sock.send_json(await asyncio.to_thread(save_screenshot))
                elif m.get("type") == "keep":
                    hub.spawn(hub.keep(m))
                elif m.get("type") == "open_item" and isinstance(m.get("id"), int):
                    await hub.open_item(m.get("kind"), m["id"])
                elif m.get("type") == "remove_vault" and isinstance(m.get("id"), int):
                    hub.spawn(hub.desk_run("remove_from_vault", id=m["id"]))
                elif m.get("type") == "full_view":
                    if inspect.iscoroutinefunction(hub.open_full_view):
                        await hub.open_full_view()
                    else:   # a plain function (e.g. launching Edge) must not block the loop
                        await asyncio.to_thread(hub.open_full_view)
                elif m.get("type") == "upload":
                    await sock.send_json(save_upload(str(m.get("name", "")),
                                                     str(m.get("data", ""))))
        except WebSocketDisconnect:
            hub.sockets.discard(sock)

    return app
