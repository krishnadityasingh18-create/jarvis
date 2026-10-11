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
    assert llm.route_order(cfg, "local") == ["local", "fast", "fast_alt2", "reasoning", "fast_alt"]


def test_make_client_uses_system_trust_store(cfg):
    import truststore
    c = llm.make_client(cfg.routes["fast"])
    assert isinstance(c._client._transport._pool._ssl_context, truststore.SSLContext)


def tc_chunk(index=0, id=None, name=None, args=None):
    call = NS(index=index, id=id, function=NS(name=name, arguments=args))
    return NS(choices=[NS(delta=NS(content=None, tool_calls=[call]))], usage=None)


def test_tool_call_assembled_from_split_deltas(cfg, conn):
    chunks = [tc_chunk(0, "c1", "open_app", ""), tc_chunk(0, None, None, '{"na'),
              tc_chunk(0, None, None, 'me":"x"}')]
    out = list(stream_reply(cfg, conn, MSGS, "fast", factory({"fast": lambda **kw: iter(chunks)}),
                            tools=[{"type": "function"}]))
    assert out == [("route", "fast"),
                   ("tool_calls", [{"id": "c1", "name": "open_app", "arguments": '{"name":"x"}'}])]


def test_tool_call_missing_id_gets_one(cfg, conn):
    chunks = [tc_chunk(None, None, "remember", '{"text":"a"}')]
    out = list(stream_reply(cfg, conn, MSGS, "fast", factory({"fast": lambda **kw: iter(chunks)}),
                            tools=[{"type": "function"}]))
    (kind, calls), = out[1:]
    assert kind == "tool_calls" and calls[0]["id"] and calls[0]["name"] == "remember"


def test_tool_only_reply_is_started(cfg, conn):
    seen = []
    chunks = [tc_chunk(0, "c1", "list_windows", "{}")]

    def fast(**kw):
        seen.append("fast")
        return iter(chunks)
    out = list(stream_reply(cfg, conn, MSGS, "fast", factory({"fast": fast}), tools=[{}]))
    assert out[0] == ("route", "fast") and out[1][0] == "tool_calls" and seen == ["fast"]


def test_no_fallback_flag(cfg, conn):
    called = []

    def reasoning(**kw):
        called.append("reasoning")
        return iter([chunk("hi")])
    with pytest.raises(AllRoutesFailed):
        list(stream_reply(cfg, conn, MSGS, "fast", factory({"reasoning": reasoning}),
                          fallback=False))
    assert called == []


def test_tools_not_sent_when_none(cfg, conn):
    kws = []

    def fast(**kw):
        kws.append(kw)
        return iter([chunk("hi")])
    list(stream_reply(cfg, conn, MSGS, "fast", factory({"fast": fast})))
    list(stream_reply(cfg, conn, MSGS, "fast", factory({"fast": fast}), tools=[{"x": 1}]))
    assert "tools" not in kws[0] and kws[1]["tools"] == [{"x": 1}]


def test_route_extra_body_passed(cfg, conn):
    kws = []

    def fast(**kw):
        kws.append(kw)
        return iter([chunk("hi")])
    cfg.routes["fast"].extra_body = {"reasoning_effort": "none"}
    list(stream_reply(cfg, conn, MSGS, "fast", factory({"fast": fast})))
    assert kws[0]["extra_body"] == {"reasoning_effort": "none"}


def test_think_tags_stripped(cfg, conn):
    out = list(stream_reply(cfg, conn, MSGS, "fast",
                            factory({"fast": ok("<think>", "</think>", "Hello sir.")})))
    assert "".join(v for k, v in out if k == "text") == "Hello sir."


SIG = {"google": {"thought_signature": "abc"}}


def test_tool_call_keeps_gemini_signature(cfg, conn):
    call = NS(index=0, id="c1", function=NS(name="remember", arguments="{}"),
              model_extra={"extra_content": SIG})
    chunks = [NS(choices=[NS(delta=NS(content=None, tool_calls=[call]))], usage=None)]
    out = list(stream_reply(cfg, conn, MSGS, "fast", factory({"fast": lambda **kw: iter(chunks)}),
                            tools=[{}]))
    assert out[-1][1][0]["extra_content"] == SIG


def history_with_calls():
    return MSGS + [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "x", "arguments": "{}"}},
            {"id": "b", "type": "function", "function": {"name": "y", "arguments": "{}"},
             "extra_content": SIG}]},
        {"role": "tool", "tool_call_id": "a", "content": "ok"},
        {"role": "tool", "tool_call_id": "b", "content": "ok"}]


def sent_calls(cfg, conn, route):
    sent = []

    def create(**kw):
        sent.append(kw["messages"])
        return iter([chunk("hi")])
    list(stream_reply(cfg, conn, history_with_calls(), route, factory({route: create}),
                      fallback=False))
    return sent[0][1]["tool_calls"]


def test_gemini_gets_signatures_for_every_replayed_call(cfg, conn):
    calls = sent_calls(cfg, conn, "reasoning")              # the Gemini route
    assert calls[0]["extra_content"]["google"]["thought_signature"] == \
        "skip_thought_signature_validator"
    assert calls[1]["extra_content"] == SIG                  # a real one is kept


def test_other_routes_never_see_signatures(cfg, conn):
    assert all("extra_content" not in c for c in sent_calls(cfg, conn, "fast"))


def test_caller_messages_not_mutated(cfg, conn):
    msgs = history_with_calls()
    list(stream_reply(cfg, conn, msgs, "reasoning",
                      factory({"reasoning": lambda **kw: iter([chunk("hi")])}), fallback=False))
    assert "extra_content" not in msgs[1]["tool_calls"][0]
