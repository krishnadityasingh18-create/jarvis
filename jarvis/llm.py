import uuid
from collections.abc import Iterator

import openai

from jarvis import db
from jarvis.config import Config, Route, get_key


class AllRoutesFailed(Exception):
    pass


class StreamBroken(Exception):
    pass


def make_client(route: Route) -> openai.OpenAI:
    key = get_key(route.key) if route.key else "ollama"
    return openai.OpenAI(base_url=route.base_url, api_key=key, max_retries=0)


# Gemini 3 rejects a replayed tool call without its "thought signature". Calls from this
# turn carry the real one; calls rebuilt from history get Google's documented placeholder.
PLACEHOLDER_SIGNATURE = {"google": {"thought_signature": "skip_thought_signature_validator"}}


def _for_route(messages: list[dict], r: Route) -> list[dict]:
    """Signatures for Gemini on every tool call; no signatures for anyone else."""
    if not any(m.get("tool_calls") for m in messages):
        return messages
    gemini = "generativelanguage.googleapis.com" in r.base_url
    out = []
    for m in messages:
        if m.get("tool_calls"):
            m = {**m, "tool_calls": [
                {**{k: v for k, v in c.items() if k != "extra_content"},
                 **({"extra_content": c.get("extra_content") or PLACEHOLDER_SIGNATURE}
                    if gemini else {})}
                for c in m["tool_calls"]]}
        out.append(m)
    return out


def route_order(cfg: Config, first: str) -> list[str]:
    return [first] + [r for r in cfg.fallback if r != first]


def stream_reply(cfg: Config, conn, messages: list[dict], route: str,
                 client_factory=make_client, tools: list[dict] | None = None,
                 fallback: bool = True) -> Iterator[tuple[str, object]]:
    """Yields ("route", name), ("text", delta)... and at most one final ("tool_calls", [...])."""
    over = db.budget_state(db.month_spend_inr(conn), cfg.monthly_inr) == "over"
    errors = []
    tool_args = {"tools": tools} if tools else {}
    for name in route_order(cfg, route) if fallback else [route]:
        r = cfg.routes[name]
        if r.key and get_key(r.key) is None:
            errors.append(f"{name}: no API key")
            continue
        if over and (r.price_in > 0 or r.price_out > 0):
            errors.append(f"{name}: budget cap reached")
            continue
        started = False
        try:
            stream = client_factory(r).chat.completions.create(
                model=r.model, messages=_for_route(messages, r), stream=True,
                stream_options={"include_usage": True}, **tool_args,
                **({"extra_body": r.extra_body} if r.extra_body else {}))
            it = iter(stream)
            text, usage, calls = "", None, {}
            for ch in it:
                usage = getattr(ch, "usage", None) or usage
                d = ch.choices[0].delta if ch.choices else None
                delta = d.content if d else None
                if delta:  # Qwen sometimes leaks its thinking tags into the reply
                    delta = delta.replace("<think>", "").replace("</think>", "")
                tcs = getattr(d, "tool_calls", None) if d else None
                if not delta and not tcs:
                    continue
                if not started:
                    started = True
                    yield ("route", name)
                for tc in tcs or []:  # Gemini may omit index and id
                    c = calls.setdefault(tc.index or 0, {"id": "", "name": "", "arguments": ""})
                    c["id"] = c["id"] or tc.id or ""
                    c["name"] += tc.function.name or ""
                    c["arguments"] += tc.function.arguments or ""
                    if extra := (getattr(tc, "model_extra", None) or {}).get("extra_content"):
                        c["extra_content"] = extra   # Gemini's thought signature
                if delta:
                    text += delta
                    yield ("text", delta)
        except openai.APIError as e:
            if started:
                raise StreamBroken(str(e)) from e
            errors.append(f"{name}: {e.__class__.__name__}")
            continue
        except Exception as e:
            if started:
                raise StreamBroken(str(e)) from e
            raise
        if not started:
            errors.append(f"{name}: empty reply")
            continue
        tin = (usage.prompt_tokens if usage
               else sum(len(str(m.get("content") or "")) for m in messages) // 4)
        tout = usage.completion_tokens if usage else len(text) // 4
        db.record_usage(conn, name, r.model, tin, tout,
                        (tin * r.price_in + tout * r.price_out) / 1e6)
        if calls:
            for c in calls.values():
                c["id"] = c["id"] or "call_" + uuid.uuid4().hex[:12]
            yield ("tool_calls", [calls[i] for i in sorted(calls)])
        return
    raise AllRoutesFailed("; ".join(errors))
