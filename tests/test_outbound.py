import pytest

from jarvis import db
from jarvis.tools import TOOLS, outbound, run
from tests.test_tools import call, make_ctx


@pytest.fixture
def setup(cfg, monkeypatch):
    sent = []

    def post(url, headers, payload):
        sent.append((url, headers, payload))
        return {"id": "call_1"}
    monkeypatch.setattr(outbound, "_post", post)
    keys = {"vapi_key": "vk", "n8n_token": "nt"}
    monkeypatch.setattr(outbound, "get_key", lambda n: keys.get(n))
    cfg.remote = {"my_number": "+919999999999", "vapi_phone_id": "ph1",
                  "vapi_assistant_id": "as1", "whatsapp_webhook": "https://n8n.local/webhook/wa"}
    return sent


async def test_call_me_needs_no_confirm_and_calls_my_number(cfg, conn, setup):
    ctx = make_ctx(cfg, conn)
    r = await run(ctx, call("call_me", reason="lab reminder"))
    url, headers, payload = setup[0]
    assert ctx.asked == [] and url == "https://api.vapi.ai/call"
    assert headers["Authorization"] == "Bearer vk"
    assert payload["customer"]["number"] == "+919999999999"
    assert payload["phoneNumberId"] == "ph1" and payload["assistantId"] == "as1"
    assert "lab reminder" in payload["assistantOverrides"]["firstMessage"]
    assert "Calling" in r


async def test_place_call_reads_back_number_and_purpose(cfg, conn, setup):
    ctx = make_ctx(cfg, conn, answer=False)
    r = await run(ctx, call("place_call", number="+911234567890", purpose="book a table"))
    assert r == "The user declined." and setup == []
    assert ctx.asked == ["Call +911234567890 to book a table, sir?"]


async def test_send_whatsapp_confirms_then_posts_to_n8n(cfg, conn, setup):
    ctx = make_ctx(cfg, conn)
    await run(ctx, call("send_whatsapp", to="+911234567890", text="Running late"))
    assert ctx.asked == ["Send WhatsApp to +911234567890: 'Running late', sir?"]
    url, headers, payload = setup[0]
    assert url == "https://n8n.local/webhook/wa" and headers["Authorization"] == "Bearer nt"
    assert payload == {"to": "+911234567890", "text": "Running late"}


async def test_missing_setup_says_what_is_needed(cfg, conn, monkeypatch):
    monkeypatch.setattr(outbound, "get_key", lambda n: None)
    cfg.remote = {}
    r = await run(make_ctx(cfg, conn), call("call_me", reason="x"))
    assert r.startswith("Error") and "keys set vapi_key" in r


async def test_bad_number_refused(cfg, conn, setup):
    r = await run(make_ctx(cfg, conn), call("place_call", number="call 911 now", purpose="x"))
    assert r.startswith("Error") and setup == []


def test_flags():
    assert not TOOLS["call_me"].confirm and TOOLS["place_call"].confirm
    assert TOOLS["send_whatsapp"].confirm and TOOLS["send_whatsapp"].exports


async def test_whatsapp_question_shows_the_whole_message(cfg, conn, setup):
    text = "Running late. " + "x" * 200
    ctx = make_ctx(cfg, conn, answer=False)
    await run(ctx, call("send_whatsapp", to="+911234567890", text=text))
    assert text in ctx.asked[0]
    r = await run(make_ctx(cfg, conn), call("send_whatsapp", to="+911234567890", text="y" * 600))
    assert r.startswith("Error")


@pytest.mark.parametrize("number", ["9999999999", "+\u0669\u0669\u0669\u0669\u0669\u0669\u0669\u0669\u0669"])
async def test_numbers_must_be_international_ascii(cfg, conn, setup, number):
    r = await run(make_ctx(cfg, conn), call("place_call", number=number, purpose="x"))
    assert r.startswith("Error") and setup == []


async def test_place_call_passes_the_purpose_as_context(cfg, conn, setup):
    cfg.remote["vapi_outbound_assistant_id"] = "out1"
    await run(make_ctx(cfg, conn), call("place_call", number="+911234567890",
                                        purpose="book a table for two at 8"))
    payload = setup[0][2]
    assert payload["assistantId"] == "out1"
    assert payload["assistantOverrides"]["variableValues"]["purpose"] == "book a table for two at 8"


def test_credits_note_counts_calls_left(cfg, monkeypatch):
    keys = {"twilio_token": "tt", "vapi_key": "vk"}
    monkeypatch.setattr(outbound, "get_key", lambda n: keys.get(n))
    cfg.remote = {"twilio_sid": "AC1", "vapi_credit_usd": 10}

    def get(url, headers=None, auth=None):
        if "Balance" in url:
            assert auth == ("AC1", "tt")
            return {"balance": "12.40", "currency": "USD"}
        if "Calls" in url:
            return {"calls": [{"price": "-0.40"}, {"price": "-0.80"}, {"price": None}]}
        assert headers == {"Authorization": "Bearer vk"}
        return [{"cost": 1.0}, {"costs": [{"cost": 0.5}, {"cost": 0.5}]}]
    monkeypatch.setattr(outbound, "_get", get)
    note = outbound.credits_note(cfg)
    assert note == ("Twilio: USD 12.40 left, about 20 calls · "
                    "Vapi: USD 8.00 left, about 8 calls")


def test_credits_note_says_what_to_set_and_survives_errors(cfg, monkeypatch):
    monkeypatch.setattr(outbound, "get_key", lambda n: "k")
    cfg.remote = {"twilio_sid": "AC1"}

    def boom(*a, **k):
        raise ValueError("api.twilio.com answered 401")
    monkeypatch.setattr(outbound, "_get", boom)
    note = outbound.credits_note(cfg)
    assert "Twilio: couldn't check (api.twilio.com answered 401)" in note
    assert "vapi_credit_usd" in note


async def test_calls_are_metered_then_settled_with_vapi_minutes_and_cost(cfg, conn, setup,
                                                                         monkeypatch):
    await run(make_ctx(cfg, conn), call("call_me", reason="x"))
    assert db.requests_today(conn)["vapi"] == 1          # counted like an LLM request
    asked = []

    def get(url, headers=None, auth=None):
        asked.append(url)
        if len(asked) == 1:
            return {"id": "call_1", "status": "in-progress"}
        return {"id": "call_1", "status": "ended", "cost": 0.5,
                "startedAt": "2026-10-12T07:00:00.000Z", "endedAt": "2026-10-12T07:02:30.000Z"}
    monkeypatch.setattr(outbound, "_get", get)
    cfg.remote["usd_inr"] = 80
    outbound.settle_calls(cfg, conn)                     # still talking: nothing yet
    assert db.month_spend_inr(conn) == 0
    outbound.settle_calls(cfg, conn)
    outbound.settle_calls(cfg, conn)                     # settled: not asked again
    assert asked == ["https://api.vapi.ai/call/call_1"] * 2
    assert conn.execute("SELECT seconds FROM usage WHERE provider='vapi'").fetchone()[0] == 150
    assert db.month_spend_inr(conn) == 40


def test_unanswered_call_counts_no_invented_minutes(cfg, conn, monkeypatch):
    monkeypatch.setattr(outbound, "get_key", lambda n: "vk")
    monkeypatch.setattr(outbound, "_get", lambda url, headers=None: {"status": "ended"})
    outbound._meter(conn, {"id": "c2"})
    outbound.settle_calls(cfg, conn)
    assert tuple(conn.execute("SELECT seconds, cost_inr FROM usage").fetchone()) == (0, 0)
