"""The away channel: Vapi (phone) and n8n (WhatsApp) talk to the same brain over HTTP.
Every request needs the bearer token; turns from here can't approve anything."""
import asyncio
import hmac
import json
import re
import time
import uuid
from collections import deque

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from jarvis.config import get_key

PATHS = ("/api/message", "/v1/chat/completions")
PER_MINUTE = 30
FILLER = "One moment, sir."
STRANGER = "Sorry, I can't verify who's calling, so I can't help on this line."
FILLER_AFTER_S = 2.5   # silence on a phone line feels like a dropped call
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


def _digits(number) -> str:
    return re.sub(r"\D", "", str(number or ""), flags=re.ASCII)


def known(cfg, number) -> bool:
    """Is this the user? The token only proves the request came from Vapi/n8n; anyone can
    call the number or message the bot, so the sender must be one of the user's numbers."""
    r = getattr(cfg, "remote", {}) or {}
    mine = {_digits(n) for n in [r.get("my_number"), *r.get("allowed_numbers", [])] if n}
    return bool(_digits(number)) and _digits(number) in mine


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "body must be JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")
    return body


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
        body = await _json(request)
        if not isinstance(body.get("text"), str) or not body["text"].strip():
            raise HTTPException(400, "no text")
        if not known(hub.cfg, body.get("from")):
            raise HTTPException(403, "unknown sender")
        text = body["text"].strip()[:MAX_TEXT]
        spoken, screen = [], []

        async def emit(m):
            if m["type"] == "spoken_sentence":
                spoken.append(m["text"])
            elif m["type"] == "screen_delta":
                screen.append(m["text"])
        try:
            await _turn(hub, text, "whatsapp", emit)
        except Exception as e:
            return {"reply": f"Something went wrong at my end, sir ({e.__class__.__name__})."}
        reply = " ".join(spoken) + (f"\n\n{''.join(screen)}" if screen else "")
        return {"reply": reply.strip()}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        """Vapi "Custom LLM": OpenAI-compatible. JARVIS's own history is the memory, so only
        the caller's latest words are used; only the spoken part goes back."""
        check(request)
        body = await _json(request)
        call = body.get("call") if isinstance(body.get("call"), dict) else {}
        caller = ((call.get("customer") or {}).get("number")
                  or (body.get("customer") or {}).get("number"))
        msgs = body.get("messages") if isinstance(body.get("messages"), list) else []
        text = _last_user_text([m for m in msgs if isinstance(m, dict)])
        cid, created, model = f"chatcmpl-{uuid.uuid4().hex[:12]}", int(time.time()), "jarvis"
        q: asyncio.Queue = asyncio.Queue()

        async def emit(m):
            if m["type"] == "spoken_sentence":
                await q.put(m["text"])
            elif m["type"] == "turn_end":
                await q.put(None)
        if not known(hub.cfg, caller):
            await q.put(STRANGER)
            await q.put(None)
            task = asyncio.create_task(asyncio.sleep(0))
        else:
            task = _turn(hub, text or "(silence)", "phone", emit)
            # a turn that crashes before turn_end must still end the stream
            task.add_done_callback(lambda _: q.put_nowait(None))

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
                while True:
                    try:
                        s = await (asyncio.wait_for(q.get(), FILLER_AFTER_S) if first
                                   else q.get())
                    except TimeoutError:
                        s = FILLER   # tools or a slow model: say something now
                    if s is None:
                        break
                    yield chunk({"role": "assistant", "content": s} if first
                                else {"content": " " + s})
                    first = False
                yield chunk({}, "stop")
                yield "data: [DONE]\n\n"
            finally:
                if not task.done():
                    task.cancel()   # the caller hung up
        return StreamingResponse(stream(), media_type="text/event-stream")
