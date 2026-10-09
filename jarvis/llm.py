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


def route_order(cfg: Config, first: str) -> list[str]:
    return [first] + [r for r in cfg.fallback if r != first]


def stream_reply(cfg: Config, conn, messages: list[dict], route: str,
                 client_factory=make_client) -> Iterator[tuple[str, str]]:
    over = db.budget_state(db.month_spend_inr(conn), cfg.monthly_inr) == "over"
    errors = []
    for name in route_order(cfg, route):
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
                model=r.model, messages=messages, stream=True,
                stream_options={"include_usage": True})
            it = iter(stream)
            text, usage = "", None
            for ch in it:
                usage = getattr(ch, "usage", None) or usage
                delta = ch.choices[0].delta.content if ch.choices else None
                if not delta:
                    continue
                if not started:
                    started = True
                    yield ("route", name)
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
        tin = usage.prompt_tokens if usage else sum(len(m["content"]) for m in messages) // 4
        tout = usage.completion_tokens if usage else len(text) // 4
        db.record_usage(conn, name, r.model, tin, tout,
                        (tin * r.price_in + tout * r.price_out) / 1e6)
        return
    raise AllRoutesFailed("; ".join(errors))
