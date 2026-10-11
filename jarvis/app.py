import asyncio
import time as clock_time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from jarvis import brain, db, remote, tools

UI = Path(__file__).parent / "ui"
ALLOWED_ORIGINS = {"http://127.0.0.1:8765", "http://localhost:8765"}
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

    def usage(self) -> dict:
        return {"type": "usage", "spent_inr": round(db.month_spend_inr(self.conn), 2),
                "cap_inr": self.cfg.monthly_inr, "requests_today": db.requests_today(self.conn)}

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
        due = self.conn.execute("SELECT id, at, text FROM reminders WHERE done=0 AND at <= ? "
                                "ORDER BY at", (now.isoformat(timespec="minutes"),)).fetchall()
        for rid, at, text in due:
            self.conn.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,))
            late = datetime.fromisoformat(at) < now - timedelta(minutes=5)
            await self.broadcast({"type": "banner",
                                  "text": f"Reminder{' (late)' if late else ''}: {text}"})
            if self.voice:
                await self.voice.speak(f"{'A late reminder' if late else 'Reminder'}, sir: {text}")
        if due or not self.cfg.briefing_enabled or \
                not self.cfg.briefing_time <= now.time() < self.BRIEFING_UNTIL:
            return
        key = f"briefed:{now.date().isoformat()}"
        if not self.conn.execute("SELECT 1 FROM kv WHERE key=?", (key,)).fetchone():
            self.conn.execute("INSERT INTO kv(key, value) VALUES(?,?)", (key, now.isoformat()))
            await self.start_turn(self.BRIEFING_PROMPT, typed=True)   # voice: idle → thinking

    async def clock(self) -> None:
        start = clock_time.monotonic()
        while True:
            try:
                if self.ready(clock_time.monotonic() - start):
                    await self.tick(datetime.now())
            except Exception as e:   # a bad row must not stop reminders for good
                await self.broadcast({"type": "banner", "text": f"Scheduler error: {e}"})
            await asyncio.sleep(30)

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


def create_app(cfg, conn, voice=None, turn_runner=brain.run_turn) -> FastAPI:
    hub = Hub(cfg, conn, voice, turn_runner)

    @asynccontextmanager
    async def lifespan(app):
        hub.loop = asyncio.get_running_loop()
        clock = asyncio.create_task(hub.clock())
        yield
        clock.cancel()

    app = FastAPI(lifespan=lifespan)
    app.state.hub = hub
    remote.routes(app, hub)   # the away channel (token-protected); the UI stays below

    @app.get("/")
    async def index():
        return FileResponse(UI / "index.html")

    app.mount("/ui", StaticFiles(directory=UI), name="ui")

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        # WebSockets bypass CORS: refuse pages from other sites (browsers always send Origin).
        origin = sock.headers.get("origin")
        if origin and origin not in ALLOWED_ORIGINS:
            await sock.close(code=1008)
            return
        await sock.accept()
        hub.loop = asyncio.get_running_loop()
        hub.sockets.add(sock)
        await sock.send_json({"type": "history", "messages": db.recent_messages(conn, 20),
                              "route": hub.route})
        await sock.send_json(hub.usage())
        await sock.send_json({"type": "actions", "actions": db.recent_actions(conn, 30)})
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
        except WebSocketDisconnect:
            hub.sockets.discard(sock)

    return app
