import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from jarvis import brain, db

UI = Path(__file__).parent / "ui"


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

    async def _stop_turn(self) -> None:
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
        speak = self.voice.speak if self.voice else _no_speak
        self.turn = asyncio.create_task(self.turn_runner(
            self.cfg, self.conn, text, brain.pick_route(text, self.route), self.broadcast, speak))

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
        else:  # state, banner
            coro = lambda: self.broadcast({"type": name, **payload})  # noqa: E731
        self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(coro()))


def create_app(cfg, conn, voice=None, turn_runner=brain.run_turn) -> FastAPI:
    hub = Hub(cfg, conn, voice, turn_runner)

    @asynccontextmanager
    async def lifespan(app):
        hub.loop = asyncio.get_running_loop()
        yield

    app = FastAPI(lifespan=lifespan)
    app.state.hub = hub

    @app.get("/")
    async def index():
        return FileResponse(UI / "index.html")

    app.mount("/ui", StaticFiles(directory=UI), name="ui")

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        hub.loop = asyncio.get_running_loop()
        hub.sockets.add(sock)
        await sock.send_json({"type": "history", "messages": db.recent_messages(conn, 20),
                              "route": hub.route})
        await sock.send_json(hub.usage())
        try:
            while True:
                m = await sock.receive_json()
                if m.get("type") == "text_input":
                    await hub.start_turn(str(m.get("text", "")))
                elif m.get("type") == "cancel":
                    await hub.cancel()
                elif m.get("type") == "set_route" and m.get("route") in cfg.routes:
                    hub.route = m["route"]
        except WebSocketDisconnect:
            hub.sockets.discard(sock)

    return app
