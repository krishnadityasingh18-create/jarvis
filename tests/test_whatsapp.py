import asyncio

import pytest

from jarvis import db, whatsapp
from jarvis.tools import run
from tests.test_tools import call, make_ctx


class FakeClient:
    def __init__(self, matches=(), send=None):
        self.matches, self.sent, self.looked_up = list(matches), [], []
        self._send = send or {"ok": True, "id": "A1"}

    def contacts(self, q):
        self.looked_up.append(q)
        return self.matches

    def send(self, number, text):
        self.sent.append((number, text))
        return self._send


@pytest.fixture
def helper(cfg, monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(whatsapp, "client", lambda data_dir: fake)
    return fake


def person(name, number):
    return {"name": name, "jid": number[1:] + "@s.whatsapp.net", "number": number}


async def test_e164_sends_without_lookup(cfg, conn, helper):
    ctx = make_ctx(cfg, conn)
    r = await run(ctx, call("send_whatsapp", to="+911234567890", text="Running late"))
    assert helper.looked_up == [] and helper.sent == [("+911234567890", "Running late")]
    assert ctx.asked == ["Send WhatsApp to +911234567890: 'Running late', sir?"]
    assert "sent" in r.lower()


async def test_two_matches_asks_which_and_asks_nothing(cfg, conn, helper):
    helper.matches = [person("Rahul Sharma", "+911111111111"), person("Rahul K", "+912222222222")]
    ctx = make_ctx(cfg, conn)
    r = await run(ctx, call("send_whatsapp", to="Rahul", text="hi"))
    assert "Which one: Rahul Sharma, Rahul K?" in r
    assert ctx.asked == [] and helper.sent == []


async def test_no_match_asks_for_the_number(cfg, conn, helper):
    ctx = make_ctx(cfg, conn)
    r = await run(ctx, call("send_whatsapp", to="Zed", text="hi"))
    assert "I don't know a WhatsApp contact called Zed. What's their number?" in r
    assert ctx.asked == [] and helper.sent == []


async def test_one_match_question_names_the_contact(cfg, conn, helper):
    helper.matches = [person("Rahul K", "+912222222222")]
    ctx = make_ctx(cfg, conn)
    await run(ctx, call("send_whatsapp", to="rahul k", text="hi"))
    assert ctx.asked == ["Send WhatsApp to Rahul K (+912222222222): 'hi', sir?"]
    assert helper.sent == [("+912222222222", "hi")]


async def test_declined_sends_nothing(cfg, conn, helper):
    ctx = make_ctx(cfg, conn, answer=False)
    r = await run(ctx, call("send_whatsapp", to="+911234567890", text="hi"))
    assert r == "The user declined." and helper.sent == []


async def test_helper_failure_is_an_error_never_sent(cfg, conn, helper):
    helper._send = {"ok": False, "error": "Connection Closed"}
    ctx = make_ctx(cfg, conn)
    r = await run(ctx, call("send_whatsapp", to="+911234567890", text="hi"))
    assert r.startswith("Error") and "Connection Closed" in r and "sent" not in r.lower()


async def test_twenty_an_hour(cfg, conn, helper):
    for _ in range(20):
        db.log_action(conn, "send_whatsapp", {}, "ok", "done", None, None)
    db.log_action(conn, "send_whatsapp", {}, "no", "declined", None, None)   # doesn't count
    ctx = make_ctx(cfg, conn)
    r = await run(ctx, call("send_whatsapp", to="+911234567890", text="hi"))
    assert "That's 20 messages this hour; I'll wait, sir." in r
    assert ctx.asked == [] and helper.sent == []


async def test_remote_is_refused(cfg, conn, helper):
    ctx = make_ctx(cfg, conn)
    ctx.remote = "whatsapp"
    r = await run(ctx, call("send_whatsapp", to="+911234567890", text="hi"))
    assert "needs the user at the desk" in r and helper.sent == [] and ctx.asked == []


async def test_no_helper_and_nothing_configured(cfg, conn):
    cfg.remote = {}
    r = await run(make_ctx(cfg, conn), call("send_whatsapp", to="+911234567890", text="hi"))
    assert r.startswith("Error") and "not set up yet" in r


def test_client_is_none_without_a_port_file(tmp_path):
    assert whatsapp.client(tmp_path) is None


def test_unreachable_helper_gives_ok_false(tmp_path):
    c = whatsapp.Client(1, "tok")   # nothing listens on port 1
    r = c.send("+911234567890", "hi")
    assert r["ok"] is False and r["error"]
    assert c.status() is None



async def test_replies_become_notify_cards(cfg, conn, monkeypatch):
    from jarvis.app import Hub
    seen = []

    class C:
        def replies(self, since):
            return [{"from": "+911", "name": "Rahul K", "text": "on my way", "ts": "x"}]
    monkeypatch.setattr(whatsapp, "client", lambda d: C())
    hub = Hub(cfg, conn, None, None)

    async def grab(m):
        seen.append(m)
        raise asyncio.CancelledError   # one round is enough
    hub.broadcast = grab
    with pytest.raises(asyncio.CancelledError):
        await hub.whatsapp_replies(interval=0)
    assert seen == [{"type": "notify", "icon": "whatsapp", "title": "Rahul K",
                     "text": "on my way"}]


async def test_whatsapp_state_without_helper(cfg, conn):
    from jarvis.app import Hub
    assert await Hub(cfg, conn, None, None).whatsapp_state() == \
        {"type": "whatsapp", "state": "unlinked", "qr": ""}
