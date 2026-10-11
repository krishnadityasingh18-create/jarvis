"""The away channel: Vapi (phone) and n8n (WhatsApp) talk to the same brain over HTTP.
Every request needs the bearer token; turns from here can't approve anything."""
import asyncio
import hmac
import json
import time
import uuid
from collections import deque

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from jarvis.config import get_key

PER_MINUTE = 30
MAX_TEXT = 2000
_hits: deque = deque()


def check(request: Request) -> None:
    token = get_key("remote_token")
    auth = request.headers.get("authorization", "")
    if not token or not hmac.compare_digest(auth.encode(), f"Bearer {token}".encode()):
        raise HTTPException(401, "unauthorized")
    now = time.monotonic()
    while _hits and now - _hits[0] > 60:
        _hits.popleft()
    if len(_hits) >= PER_MINUTE:
        raise HTTPException(429, "too many requests")
    _hits.append(now)


async def _no_speak(text: str) -> None:
    pass


def _turn(hub, text: str, channel: str, emit) -> asyncio.Task:
    return asyncio.create_task(hub.turn_runner(hub.cfg, hub.conn, text, hub.route, emit,
                                               _no_speak, channel=channel))


def _last_user_text(messages: list) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user":
            c = m.get("content", "")
            if isinstance(c, list):   # OpenAI "content parts"
                c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
            return str(c).strip()[:MAX_TEXT]
    return ""


def routes(app, hub) -> None:
    @app.post("/api/message")
    async def message(request: Request):
        """n8n (WhatsApp): {"text": ...} in, {"reply": ...} out."""
        check(request)
        text = str((await request.json()).get("text", "")).strip()[:MAX_TEXT]
        if not text:
            raise HTTPException(400, "no text")
        spoken, screen = [], []

        async def emit(m):
            if m["type"] == "spoken_sentence":
                spoken.append(m["text"])
            elif m["type"] == "screen_delta":
                screen.append(m["text"])
        await _turn(hub, text, "whatsapp", emit)
        reply = " ".join(spoken) + (f"\n\n{''.join(screen)}" if screen else "")
        return {"reply": reply.strip()}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        """Vapi "Custom LLM": OpenAI-compatible. JARVIS's own history is the memory, so only
        the caller's latest words are used; only the spoken part goes back."""
        check(request)
        body = await request.json()
        text = _last_user_text(body.get("messages"))
        cid, created, model = f"chatcmpl-{uuid.uuid4().hex[:12]}", int(time.time()), "jarvis"
        q: asyncio.Queue = asyncio.Queue()

        async def emit(m):
            if m["type"] == "spoken_sentence":
                await q.put(m["text"])
            elif m["type"] == "turn_end":
                await q.put(None)
        task = _turn(hub, text or "(silence)", "phone", emit)

        def chunk(delta: dict, finish=None) -> str:
            return "data: " + json.dumps({"id": cid, "object": "chat.completion.chunk",
                                          "created": created, "model": model, "choices": [
                                              {"index": 0, "delta": delta,
                                               "finish_reason": finish}]}) + "\n\n"

        if not body.get("stream"):
            parts = []
            while (s := await q.get()) is not None:
                parts.append(s)
            await task
            return {"id": cid, "object": "chat.completion", "created": created, "model": model,
                    "choices": [{"index": 0, "finish_reason": "stop", "message": {
                        "role": "assistant", "content": " ".join(parts)}}]}

        async def stream():
            first = True
            try:
                while (s := await q.get()) is not None:
                    yield chunk({"role": "assistant", "content": s} if first
                                else {"content": " " + s})
                    first = False
                yield chunk({}, "stop")
                yield "data: [DONE]\n\n"
            finally:
                if not task.done():
                    task.cancel()   # the caller hung up
        return StreamingResponse(stream(), media_type="text/event-stream")
