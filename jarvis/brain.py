import asyncio
import json
import re
import threading
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path

from jarvis import db, tools
from jarvis.llm import StreamBroken, stream_reply
from jarvis.tools.memory import memory_block

Emit = Callable[[dict], Awaitable[None]]
Speak = Callable[[str], Awaitable[None]]

LOST = "I lost the connection, sir."
NO_MODEL = "I can't reach any model right now, sir."
NO_LOCAL = "I can't reach the local model, sir, and your files stay on this laptop."
REDACTED = "[answer about the user's files, kept on this laptop]"
BUDGET = "Sir, we've used 80 percent of this month's budget."
STOPPED = "I've stopped after five steps, sir."
MAX_SPOKEN = 2
MAX_STEPS = 5

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
        self.unescape = False   # the model wrote "\\n" (backslash, n) instead of newlines

    def feed(self, delta: str) -> list[tuple[str, str]]:
        if self.screen_mode:
            if self.unescape:
                # ponytail: misses a backslash and n split across two deltas; rare, cosmetic
                delta = delta.replace("\\n", "\n")
            return [("screen", delta)] if delta else []
        self.buf += delta
        if "---\\n" in self.buf:
            self.unescape = True
            self.buf = self.buf.replace("\\n", "\n")
        out = []
        while not self.screen_mode and (m := _next_boundary(self.buf)):
            piece, self.buf = self.buf[:m.end()], self.buf[m.end():]
            if m.group() == "\n" and piece.rstrip().endswith("---"):
                out += self._sentence(piece.rstrip()[:-3])  # "sir.---": models glue it on
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


async def _aiter(gen):
    """Iterate a blocking generator without blocking the event loop; stops it on cancel."""
    q: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()
    threading.Thread(target=_pump, args=(gen, asyncio.get_running_loop(), q, stop),
                     daemon=True).start()
    try:
        while (item := await q.get()) is not _END:
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        stop.set()


def _history(conn, redact: bool = False) -> list[dict]:
    """Past turns as the model wrote them: tool calls and results, then the spoken part,
    --- and the screen detail. Actions go back as real tool messages: a text summary gets
    imitated ("(actions: done)") instead of the tools being called again.
    redact: hide what private turns read and said (for any route but `local`)."""
    out = []
    for n, m in enumerate(db.recent_messages(conn, 20)):
        hide = redact and m.get("private")
        acts = json.loads(m["actions"]) if (m.get("actions") or "").startswith("[") else []
        if acts:
            ids = [f"h{n}_{i}" for i in range(len(acts))]
            out.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": i, "type": "function",
                 "function": {"name": a["name"],
                              "arguments": "{}" if hide else a["arguments"]}}
                for i, a in zip(ids, acts)]})
            out += [{"role": "tool", "tool_call_id": i,
                     "content": REDACTED if hide else a["result"]}
                    for i, a in zip(ids, acts)]
        content = m["content"] + ("\n---\n" + m["screen"] if m["screen"] else "")
        if hide and content.strip():
            content = REDACTED
        if content.strip():
            out.append({"role": m["role"], "content": content})
    while out and out[0]["role"] != "user":  # Gemini rejects a tool call not after a user turn
        out.pop(0)
    return out


async def agent_loop(ctx: tools.Ctx, messages: list[dict], route: str, tool_names: list[str],
                     fallback: bool = True, on_event=None) -> str:
    """Up to MAX_STEPS model calls, running tool calls in between. Returns all text.

    on_event(kind, value) is awaited for ("route", name), ("text", delta), ("step_end", None),
    ("tools", None) just before tools run, and ("capped", None) when the step cap is hit.
    """
    async def ev(kind, value=None):
        if on_event:
            await on_event(kind, value)
    texts = []
    schemas = tools.schemas(tool_names) or None
    for _ in range(MAX_STEPS):
        step_text, calls = "", None
        # once file contents were read this turn, only the local model sees the rest of it
        step_route, step_fallback = ("local", False) if ctx.private else (route, fallback)
        async for kind, value in _aiter(stream_reply(ctx.cfg, ctx.conn, messages, step_route,
                                                     tools=schemas, fallback=step_fallback)):
            if kind == "tool_calls":
                calls = value
                continue
            if kind == "text":
                step_text += value
            await ev(kind, value)
        texts.append(step_text)
        await ev("step_end")
        if not calls:
            return "".join(texts).strip()
        messages.append({"role": "assistant", "content": step_text, "tool_calls": [
            {"id": c["id"], "type": "function",
             "function": {"name": c["name"], "arguments": c["arguments"] or "{}"},
             **({"extra_content": c["extra_content"]} if c.get("extra_content") else {})}
            for c in calls]})
        await ev("tools")
        declined = []
        for c in calls:
            result = await tools.run(ctx, c)
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
            if result == tools.DECLINED:
                declined.append(c["name"])
        if declined and ctx.agent:   # a "no" ends a sub-agent's work; it doesn't try again
            return "".join(texts).strip() + f"\n\n(The user declined {', '.join(declined)}.)"
    await ev("capped")
    return "".join(texts).strip() + "\n\n(Stopped after five steps.)"


def _places() -> str:
    """The user's real folders, so the model doesn't guess paths (it once invented a user name)."""
    home = Path.home()
    out = [f"home: {home}"]
    for name in ("Desktop", "Documents", "Downloads"):
        for p in (home / "OneDrive" / name, home / name):   # OneDrive moves Desktop/Documents
            if p.is_dir():
                out.append(f"{name}: {p}")
                break
    return "The user's folders (use these exact paths): " + "; ".join(out) + "."


# "ask Claude ..." (Whisper often hears "cloud"/"clawed"): a direct line, no model in between
_DIRECT = re.compile(r"^\W*(?:hey\W+)?(?:jarvis\W+)?ask\s+(?:claude|cloud|clawed|claud|clod)\b"
                     r"[\s,:;.\-]*(?P<prompt>\S.*)$", re.IGNORECASE | re.DOTALL)
ON_SCREEN = "Claude's answer is on screen, sir."
SPEAK_MAX = 400


def _plan_line(plan: str, tainted: bool) -> str:
    if tainted:   # written while outside text (email, GitHub...) was in play: data only
        return f'\n\nToday\'s plan:\n<untrusted source="plan">\n{plan}\n</untrusted>'
    return f"\n\nToday's plan: {plan}"


_BRIEFING = re.compile(r"\b(brief me|(morning|daily|today'?s) briefing)\b", re.IGNORECASE)


def wants_briefing(text: str) -> bool:
    return bool(_BRIEFING.search(text))


def direct_claude_prompt(text: str) -> str | None:
    m = _DIRECT.match(text)
    return m.group("prompt").strip() if m else None


async def _direct_claude(cfg, conn, prompt: str, emit: Emit, speak: Speak) -> None:
    """Send the prompt exactly as said; give back Claude's reply exactly as written. Short
    plain replies are also read aloud word for word; code or long replies only on screen."""
    from jarvis.tools import claude
    ctx = tools.Ctx(cfg, conn, _refuse, emit)
    try:
        await emit({"type": "state", "state": "thinking"})
        reply = (await claude.direct(ctx, prompt)).strip() or "(Claude returned nothing.)"
        await emit({"type": "state", "state": "speaking"})
        if len(reply) <= SPEAK_MAX and "```" not in reply:
            await emit({"type": "spoken_sentence", "text": reply})
            db.add_message(conn, "assistant", reply, "", "claude")
            await speak(reply)
        else:
            await emit({"type": "screen_delta", "text": reply})
            await emit({"type": "spoken_sentence", "text": ON_SCREEN})
            db.add_message(conn, "assistant", ON_SCREEN, reply, "claude")
            await speak(ON_SCREEN)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        await emit({"type": "banner", "text": f"Claude Code failed: {e}"})
        await speak("Claude Code couldn't run, sir.")
    finally:
        await emit({"type": "turn_end"})


async def _refuse(question: str) -> bool:
    return False


async def run_turn(cfg, conn, user_text: str, ui_route: str, emit: Emit, speak: Speak,
                   prepare: Callable[[str], None] | None = None,
                   confirm: Callable[[str], Awaitable[bool]] | None = None) -> None:
    global _warned_on
    db.add_message(conn, "user", user_text)
    if (prompt := direct_claude_prompt(user_text)) is not None:
        return await _direct_claude(cfg, conn, prompt, emit, speak)
    from jarvis.tools import briefing as briefing_tool
    from jarvis.tools import day
    t = day.now()
    system = (cfg.persona + "\n\n" + _places()
              + f"\n\nNow: {t:%A %d %B %Y, %H:%M} (local time)."
              + (_plan_line(plan, day.today_plan_tainted(conn))
                 if (plan := day.today_plan(conn)) else "")
              + (f"\n\n{mb}" if (mb := memory_block(conn)) else ""))
    messages = [{"role": "system", "content": system}] + _history(conn,
                                                                   redact=True)
    ctx = tools.Ctx(cfg, conn, confirm or _refuse, emit)

    spoken: list[str] = []
    screen: list[str] = []
    route = ""
    sentences: asyncio.Queue = asyncio.Queue()
    speaking_started = False
    banner_shown = False
    splitter = Splitter()

    async def speaker():
        while (s := await sentences.get()) is not None:
            await emit({"type": "spoken_sentence", "text": s})
            await speak(s)
            spoken.append(s)
            sentences.task_done()

    async def say(s: str):
        nonlocal speaking_started
        if not speaking_started:
            speaking_started = True
            await emit({"type": "state", "state": "speaking"})
        if prepare:
            prepare(s)  # synthesise now, while earlier sentences are still playing
        sentences.put_nowait(s)

    async def out(pieces):
        for k, v in pieces:
            if k == "speak":
                await say(v)
            else:
                screen.append(v)
                await emit({"type": "screen_delta", "text": v})

    async def on_event(kind, value):
        nonlocal route, splitter, speaking_started, banner_shown
        if kind == "route":
            route = value
            await emit({"type": "route", "route": value})
            if value != ui_route and not banner_shown and not ctx.private:
                banner_shown = True
                await emit({"type": "banner",
                            "text": f"{ui_route} unavailable, answered via {value}"})
        elif kind == "text":
            await out(splitter.feed(value))
        elif kind == "step_end":
            await out(splitter.flush())
            spoken_so_far, splitter = splitter.spoken, Splitter()
            splitter.spoken = spoken_so_far   # the two-sentence limit is per turn
        elif kind == "tools":
            await sentences.join()   # finish speaking before acting or asking to confirm
            speaking_started = False
        elif kind == "capped":
            await say(STOPPED)

    speaker_task = asyncio.create_task(speaker())
    await emit({"type": "state", "state": "thinking"})
    saved = False
    try:
        try:
            if wants_briefing(user_text):
                # gather the real data first: left to itself a model may delegate the
                # briefing to an agent that has no data and invents one
                result = await tools.run(ctx, {"id": "brief_0", "name": "daily_briefing",
                                               "arguments": "{}"})
                messages += [{"role": "assistant", "content": "", "tool_calls": [
                    {"id": "brief_0", "type": "function",
                     "function": {"name": "daily_briefing", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "brief_0", "content": result},
                    {"role": "system", "content": briefing_tool.COMPOSE}]
            await agent_loop(ctx, messages, ui_route, list(tools.TOOLS), on_event=on_event)
        except StreamBroken:
            await say(LOST)
        except Exception as e:  # AllRoutesFailed, or anything unexpected
            await emit({"type": "banner", "text": str(e) or type(e).__name__})
            await say(NO_LOCAL if ctx.private else NO_MODEL)
        await out(splitter.flush())
        sentences.put_nowait(None)
        await speaker_task
        db.add_message(conn, "assistant", " ".join(spoken), "".join(screen), route,
                       actions=json.dumps(ctx.actions) if ctx.actions else "",
                       private=ctx.private)
        saved = True

        if db.budget_state(db.month_spend_inr(conn), cfg.monthly_inr) != "ok" \
                and _warned_on != date.today():
            _warned_on = date.today()
            await emit({"type": "banner", "text": BUDGET})
            await speak(BUDGET)
    except asyncio.CancelledError:
        speaker_task.cancel()
        if not saved:
            db.add_message(conn, "assistant", " ".join(spoken), "".join(screen), route,
                           interrupted=True, private=ctx.private,
                           actions=json.dumps(ctx.actions) if ctx.actions else "")
        raise
    finally:
        await emit({"type": "turn_end"})
