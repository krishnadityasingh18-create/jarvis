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

# A sentence ends at . ? ! followed by whitespace (so "3.5" never splits), or at a newline...
_BOUNDARY = re.compile(r"[.?!](?=\s)|\n")
# ...unless the period closes an abbreviation or a numbered-list marker ("1. foo").
_NOT_END = re.compile(r"(?:^|\s)(?:mr|mrs|ms|dr|prof|vs|etc|approx|fig|no|st|e\.g|i\.e)\.$"
                      r"|(?:^|\n)\s*\d+\.$", re.IGNORECASE)


def _next_boundary(buf: str) -> re.Match | None:
    pos = 0
    while m := _BOUNDARY.search(buf, pos):
        if m.group() == "\n" or not _NOT_END.search(buf[:m.end()]):
            return m
        pos = m.end()
    return None

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
        while not self.screen_mode and (m := _next_boundary(self.buf)):
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


def _history(conn) -> list[dict]:
    """Past turns as the model wrote them: spoken part, then --- and the screen detail."""
    out = []
    for m in db.recent_messages(conn, 20):
        content = m["content"] + ("\n---\n" + m["screen"] if m["screen"] else "")
        if content.strip():
            out.append({"role": m["role"], "content": content})
    return out


async def run_turn(cfg, conn, user_text: str, ui_route: str, emit: Emit, speak: Speak,
                   prepare: Callable[[str], None] | None = None) -> None:
    global _warned_on
    db.add_message(conn, "user", user_text)
    messages = [{"role": "system", "content": cfg.persona}] + _history(conn)

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
        if prepare:
            prepare(s)  # synthesise now, while earlier sentences are still playing
        sentences.put_nowait(s)

    q: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()
    threading.Thread(target=_pump, args=(stream_reply(cfg, conn, messages, ui_route),
                                         asyncio.get_running_loop(), q, stop),
                     daemon=True).start()
    speaker_task = asyncio.create_task(speaker())
    splitter = Splitter()
    await emit({"type": "state", "state": "thinking"})
    saved = False
    try:
        while (item := await q.get()) is not _END:
            if isinstance(item, Exception):
                if isinstance(item, StreamBroken):
                    await say(LOST)
                else:  # AllRoutesFailed, or anything unexpected from the stream
                    await emit({"type": "banner", "text": str(item) or type(item).__name__})
                    await say(NO_MODEL)
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
        db.add_message(conn, "assistant", " ".join(spoken), "".join(screen), route)
        saved = True

        if db.budget_state(db.month_spend_inr(conn), cfg.monthly_inr) != "ok" \
                and _warned_on != date.today():
            _warned_on = date.today()
            await emit({"type": "banner", "text": BUDGET})
            await speak(BUDGET)
    except asyncio.CancelledError:
        stop.set()
        speaker_task.cancel()
        if not saved:
            db.add_message(conn, "assistant", " ".join(spoken), "".join(screen), route,
                           interrupted=True)
        raise
    finally:
        await emit({"type": "turn_end"})
