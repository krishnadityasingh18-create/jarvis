from types import SimpleNamespace as NS

import httpx
import openai
import pytest

from jarvis import db, llm
from jarvis.llm import AllRoutesFailed, StreamBroken, stream_reply

MSGS = [{"role": "user", "content": "hi"}]
REQ = httpx.Request("POST", "http://x")


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setattr(llm, "get_key", lambda n: "k")


def chunk(t):
    return NS(choices=[NS(delta=NS(content=t))], usage=None)


def ok(*parts):
    return lambda **kw: iter([chunk(p) for p in parts])


def raises(exc):
    def f(**kw):
        raise exc
    return f


def ok_then_raise(first):
    def gen():
        yield chunk(first)
        raise openai.APIConnectionError(request=REQ)
    return lambda **kw: gen()


def rate_limit_error():
    return openai.RateLimitError("rl", response=httpx.Response(429, request=REQ), body=None)


def factory(behaviours):
    def make(route):
        create = behaviours.get(route.name, raises(openai.APIConnectionError(request=REQ)))
        return NS(chat=NS(completions=NS(create=create)))
    return make


def test_happy_path_yields_route_then_text(cfg, conn):
    assert list(stream_reply(cfg, conn, MSGS, "fast", factory({"fast": ok("Hi", " sir.")}))) == \
        [("route", "fast"), ("text", "Hi"), ("text", " sir.")]


def test_429_falls_back_to_reasoning(cfg, conn):
    events = list(stream_reply(cfg, conn, MSGS, "fast",
                               factory({"fast": raises(rate_limit_error()), "reasoning": ok("Hello.")})))
    assert events[0] == ("route", "reasoning")


def test_connection_error_falls_back(cfg, conn):
    events = list(stream_reply(cfg, conn, MSGS, "fast", factory({"reasoning": ok("Hello.")})))
    assert events[0] == ("route", "reasoning")


def test_break_after_first_token_raises_streambroken(cfg, conn):
    with pytest.raises(StreamBroken):
        list(stream_reply(cfg, conn, MSGS, "fast", factory({"fast": ok_then_raise("Hi")})))


def test_all_fail_raises(cfg, conn):
    with pytest.raises(AllRoutesFailed):
        list(stream_reply(cfg, conn, MSGS, "fast", factory({})))


def test_missing_key_routes_skipped(cfg, conn, monkeypatch):
    monkeypatch.setattr(llm, "get_key", lambda n: None)
    events = list(stream_reply(cfg, conn, MSGS, "fast",
                               factory({"fast": ok("A"), "local": ok("Hi.")})))
    assert events[0] == ("route", "local")


def test_paid_route_skipped_when_over_budget(cfg, conn):
    cfg.routes["fast"].price_in = 10
    db.record_usage(conn, "x", "m", 0, 0, 300)
    events = list(stream_reply(cfg, conn, MSGS, "fast",
                               factory({"fast": ok("A"), "reasoning": ok("B")})))
    assert events[0] == ("route", "reasoning")


def test_usage_row_written(cfg, conn):
    list(stream_reply(cfg, conn, MSGS, "fast", factory({"fast": ok("Hi")})))
    assert db.requests_today(conn) == {"fast": 1}


def test_route_order_puts_choice_first(cfg):
    assert llm.route_order(cfg, "local") == ["local", "fast", "reasoning"]


def test_make_client_uses_system_trust_store(cfg):
    import truststore
    c = llm.make_client(cfg.routes["fast"])
    assert isinstance(c._client._transport._pool._ssl_context, truststore.SSLContext)
