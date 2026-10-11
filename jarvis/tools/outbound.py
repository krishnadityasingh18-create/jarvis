"""Calls and messages out: Vapi (phone) and the user's n8n WhatsApp workflow."""
import re
import ssl

from jarvis.config import get_key
from jarvis.tools import tool

VAPI_CALL = "https://api.vapi.ai/call"
_NUMBER = re.compile(r"^\+\d{8,15}$", re.ASCII)   # E.164, as Vapi and WhatsApp need
MAX_MESSAGE = 500


def _post(url: str, headers: dict, payload: dict) -> dict:
    import httpx
    import truststore
    r = httpx.post(url, json=payload, headers=headers, timeout=20,
                   verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
    if r.status_code >= 400:
        raise ValueError(f"{url.split('/')[2]} answered {r.status_code}: {r.text[:200]}")
    return r.json() if r.content else {}


def _need(cfg, *names: str) -> dict:
    """The settings a tool needs, or a message saying exactly what to set."""
    r = getattr(cfg, "remote", {}) or {}
    missing = [f"[remote] {n} in config.toml" for n in names if n not in ("vapi_key", "n8n_token")
               and not r.get(n)]
    keys = {n: get_key(n) for n in names if n in ("vapi_key", "n8n_token")}
    missing += [f"python -m jarvis keys set {n}" for n, v in keys.items() if not v and
                n == "vapi_key"]
    if missing:
        raise ValueError("not set up yet; needs " + ", ".join(missing))
    return {**r, **keys}


def _number(n: str) -> str:
    n = re.sub(r"[\s\-()]", "", str(n))
    if not _NUMBER.match(n):
        raise ValueError(f"'{n}' isn't a phone number with its country code, like +91…")
    return n


def _vapi_call(s: dict, number: str, assistant: str, first: str) -> dict:
    return _post(VAPI_CALL, {"Authorization": f"Bearer {s['vapi_key']}"}, {
        "phoneNumberId": s["vapi_phone_id"], "assistantId": assistant,
        "customer": {"number": number},
        "assistantOverrides": {"firstMessage": first}})


@tool("Phone the user (their own number) through Vapi, e.g. when they ask to be called "
      "about something.", exports=True, reason="string: what the call is about")
def call_me(ctx, reason):
    s = _need(ctx.cfg, "vapi_key", "vapi_phone_id", "vapi_assistant_id", "my_number")
    c = _vapi_call(s, _number(s["my_number"]), s["vapi_assistant_id"],
                   f"Hello sir, it's JARVIS. {reason}")
    return f"Calling you now ({c.get('id', 'call started')}).", None


@tool("Phone someone else for the user. Only when the user asks; JARVIS reads the number and "
      "purpose back first.", confirm=True, exports=True,
      describe=lambda a: f"call {a.get('number', '')} to {a.get('purpose', '')}",
      number="string: phone number with country code", purpose="string: why you are calling")
def place_call(ctx, number, purpose):
    n = _number(number)
    # a separate Vapi assistant: the JARVIS one would talk to a stranger as if to "sir"
    s = _need(ctx.cfg, "vapi_key", "vapi_phone_id", "vapi_outbound_assistant_id")
    c = _post(VAPI_CALL, {"Authorization": f"Bearer {s['vapi_key']}"}, {
        "phoneNumberId": s["vapi_phone_id"], "assistantId": s["vapi_outbound_assistant_id"],
        "customer": {"number": n},
        # the outbound assistant's prompt uses {{purpose}} for what the call is about
        "assistantOverrides": {"variableValues": {"purpose": purpose},
                               "firstMessage": "Hello, I'm an assistant calling on someone's "
                                               f"behalf about this: {purpose}."}})
    return f"Calling {n} ({c.get('id', 'call started')}).", None


@tool("Send a WhatsApp message through the user's n8n workflow. Only when the user asks; "
      "asks first.", confirm=True, exports=True,
      describe=lambda a: f"send WhatsApp to {a.get('to', '')}: '{a.get('text', '')}'",
      to="string: phone number with country code", text="string: the message")
def send_whatsapp(ctx, to, text):
    n = _number(to)
    if len(text) > MAX_MESSAGE:   # the question shows the whole text, so keep it readable
        raise ValueError(f"keep the message under {MAX_MESSAGE} characters")
    s = _need(ctx.cfg, "whatsapp_webhook", "n8n_token")
    headers = {"Authorization": f"Bearer {s['n8n_token']}"} if s.get("n8n_token") else {}
    _post(s["whatsapp_webhook"], headers, {"to": n, "text": text})
    return f"WhatsApp sent to {n}.", None
