import asyncio
import re
import threading
from collections.abc import Awaitable, Callable
from datetime import date

from jarvis import db
from jarvis.llm import AllRoutesFailed, StreamBroken, stream_reply

Emit = Callable[[dict], Awaitable[None]]
Speak = Callable[[str], Awaitable[None]]

LOST = "I lost the connection, sir."
NO_MODEL = "I can't reach any model right now, sir."
BUDGET = "Sir, we've used 80 percent of this month's budget."
MAX_SPOKEN = 2

# A sentence ends at . ? ! followed by whitespace (so "3.5" never splits), or at a newline.
_BOUNDARY = re.compile(r"[.?!](?=\s)|\n")

_warned_on: date | None = None


class Splitter:
    """Streams LLM text into ("speak", sentence) and ("screen", text) events."""

    def __init__(self):
        self.buf = ""
        self.screen_mode = False
        self.spoken = 0
        self.overflow = 0

    def feed(self, delta: str) -> list[tuple[str, str]]:
        if self.screen_mode:
            return [("screen", delta)] if delta else []
        self.buf += delta
        out = []
        while not self.screen_mode and (m := _BOUNDARY.search(self.buf)):
            piece, self.buf = self.buf[:m.end()], self.buf[m.end():]
            if m.group() == "\n" and piece.strip() == "---":
                self.screen_mode = True
                rest, self.buf = self.buf, ""
                if rest:
                    out.append(("screen", rest))
            else:
                out += self._sentence(piece)
        return out

    def flush(self) -> list[tuple[str, str]]:
        rest, self.buf = self.buf, ""
        if self.screen_mode or rest.strip() == "---":
            return []
        return self._sentence(rest)

    def _sentence(self, s: str) -> list[tuple[str, str]]:
        s = s.strip()
        if not s:
            return []
        if self.spoken < MAX_SPOKEN:
            self.spoken += 1
            return [("speak", s)]
        self.overflow += 1
        return [("screen", s if self.overflow == 1 else " " + s)]


def pick_route(text: str, ui_route: str) -> str:
    return "reasoning" if text.strip().lower().startswith("think carefully") else ui_route


_END = object()


def _pump(gen, loop, q: asyncio.Queue, stop: threading.Event) -> None:
    """Runs the blocking LLM stream in a daemon thread, feeding the asyncio queue."""
    def put(item):
        try:
            loop.call_soon_threadsafe(q.put_nowait, item)
        except RuntimeError:  # loop already closed after a cancel
            pass
    try:
        for ev in gen:
            if stop.is_set():
                return
            put(ev)
        put(_END)
    except Exception as e:
        put(e)


async def run_turn(cfg, conn, user_text: str, ui_route: str, emit: Emit, speak: Speak) -> None:
    global _warned_on
    db.add_message(conn, "user", user_text)
    messages = [{"role": "system", "content": cfg.persona}] + [
        {"role": m["role"], "content": m["content"]} for m in db.recent_messages(conn, 20)]

    spoken: list[str] = []
    screen: list[str] = []
    route = ""
    sentences: asyncio.Queue = asyncio.Queue()
    speaking_started = False

    async def speaker():
        while (s := await sentences.get()) is not None:
            await emit({"type": "spoken_sentence", "text": s})
            await speak(s)
            spoken.append(s)

    async def say(s: str):
        nonlocal speaking_started
        if not speaking_started:
            speaking_started = True
            await emit({"type": "state", "state": "speaking"})
        sentences.put_nowait(s)

    q: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()
    threading.Thread(target=_pump, args=(stream_reply(cfg, conn, messages, ui_route),
                                         asyncio.get_running_loop(), q, stop),
                     daemon=True).start()
    speaker_task = asyncio.create_task(speaker())
    splitter = Splitter()
    await emit({"type": "state", "state": "thinking"})
    try:
        while (item := await q.get()) is not _END:
            if isinstance(item, Exception):
                if isinstance(item, StreamBroken):
                    await say(LOST)
                elif isinstance(item, AllRoutesFailed):
                    await emit({"type": "banner", "text": str(item)})
                    await say(NO_MODEL)
                else:
                    raise item
                break
            kind, value = item
            if kind == "route":
                route = value
                await emit({"type": "route", "route": value})
                if value != ui_route:
                    await emit({"type": "banner",
                                "text": f"{ui_route} unavailable, answered via {value}"})
                continue
            for k, v in splitter.feed(value):
                if k == "speak":
                    await say(v)
                else:
                    screen.append(v)
                    await emit({"type": "screen_delta", "text": v})
        for k, v in splitter.flush():
            if k == "speak":
                await say(v)
            else:
                screen.append(v)
                await emit({"type": "screen_delta", "text": v})
        sentences.put_nowait(None)
        await speaker_task
    except asyncio.CancelledError:
        stop.set()
        speaker_task.cancel()
        db.add_message(conn, "assistant", " ".join(spoken), "".join(screen), route,
                       interrupted=True)
        await emit({"type": "turn_end"})
        raise

    db.add_message(conn, "assistant", " ".join(spoken), "".join(screen), route)

    if db.budget_state(db.month_spend_inr(conn), cfg.monthly_inr) != "ok" \
            and _warned_on != date.today():
        _warned_on = date.today()
        await emit({"type": "banner", "text": BUDGET})
        await speak(BUDGET)
    await emit({"type": "turn_end"})
