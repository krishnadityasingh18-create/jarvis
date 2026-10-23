import asyncio

import pytest

from jarvis import db, whatsapp
from jarvis.tools import run
from tests.test_tools import call, make_ctx


class FakeClient:
    def __init__(self, matches=(), send=None):
        self.matches, self.sent, self.looked_up = list(matches), [], []
        self._send = send or {"ok": True, "id": "A1"}

    def status(self):
        return {"state": self.state, "me": "+919000000001"}

    state = "linked"

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


@pytest.mark.parametrize("to", ["myself", "Me", "self"])
async def test_myself_is_the_linked_number(cfg, conn, helper, to):
    ctx = make_ctx(cfg, conn)
    await run(ctx, call("send_whatsapp", to=to, text="test"))
    assert helper.looked_up == [] and helper.sent == [("+919000000001", "test")]
    assert ctx.asked == ["Send WhatsApp to you (+919000000001): 'test', sir?"]


async def test_myself_when_unlinked_says_so(cfg, conn, helper):
    helper.state = "qr"
    ctx = make_ctx(cfg, conn)
    r = await run(ctx, call("send_whatsapp", to="myself", text="test"))
    assert "isn't linked" in r and helper.sent == []


async def test_two_matches_asks_which_and_asks_nothing(cfg, conn, helper):
    helper.matches = [person("Rahul Sharma", "+911111111111"), person("Rahul K", "+912222222222")]
    ctx = make_ctx(cfg, conn)
    r = await run(ctx, call("send_whatsapp", to="Rahul", text="hi"))
    assert "Which one: Rahul Sharma, Rahul K?" in r
    assert ctx.asked == [] and helper.sent == []


async def test_no_match_asks_for_the_number(cfg, conn, helper):
    ctx = make_ctx(cfg, conn)
    r = await run(ctx, call("send_whatsapp", to="Zed", text="hi"))
    assert "I don't know a WhatsApp contact called Zed. What's their number? I can remember it for next time." in r
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
        def status(self):
            return {"state": "linked"}

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


async def test_whatsapp_state_without_helper(cfg, conn, monkeypatch):
    from jarvis.app import Hub
    assert await Hub(cfg, conn, None, None).whatsapp_state() == \
        {"type": "whatsapp", "state": "unlinked", "qr": ""}


async def test_remote_block_is_absolute_even_if_allowed(cfg, conn, helper):
    cfg.remote = {"allow": ["send_whatsapp"]}
    ctx = make_ctx(cfg, conn)
    ctx.remote = "phone"
    r = await run(ctx, call("send_whatsapp", to="+911234567890", text="hi"))
    assert r.startswith("Error") and "at the desk" in r and helper.sent == [] and ctx.asked == []


async def test_a_reply_is_shown_once(cfg, conn, monkeypatch):
    from jarvis.app import Hub
    calls, seen = [], []

    class C:
        def status(self):
            return {"state": "linked"}

        def replies(self, since):
            calls.append(since)
            return [{"from": "+911", "name": "R", "text": "yo", "ts": "2999-01-01T00:00:00.000Z"}]
    monkeypatch.setattr(whatsapp, "client", lambda d: C())
    hub = Hub(cfg, conn, None, None)

    async def grab(m):
        seen.append(m)
        if len(calls) == 2:
            raise asyncio.CancelledError
    hub.broadcast = grab
    with pytest.raises(asyncio.CancelledError):
        await hub.whatsapp_replies(interval=0)
    assert calls[1] == "2999-01-01T00:00:00.000Z"


async def test_unlinked_helper_with_webhook_posts_to_n8n(cfg, conn, helper, monkeypatch):
    helper.state = "unlinked"
    cfg.remote = {"whatsapp_webhook": "https://n8n.example/hook", "n8n_token": "t"}
    posts = []
    monkeypatch.setattr("jarvis.tools.outbound._post", lambda url, h, p: posts.append((url, p)) or {})
    r = await run(make_ctx(cfg, conn), call("send_whatsapp", to="+911234567890", text="hi"))
    assert helper.sent == [] and posts == [("https://n8n.example/hook", {"to": "+911234567890", "text": "hi"})]
    assert "sent" in r.lower()


async def test_name_with_unlinked_helper_says_not_linked(cfg, conn, helper):
    helper.state = "unlinked"
    ctx = make_ctx(cfg, conn)
    r = await run(ctx, call("send_whatsapp", to="Rahul", text="hi"))
    assert "isn't linked" in r and helper.looked_up == [] and ctx.asked == []


async def test_prepare_runs_off_the_event_loop(cfg, conn, helper, monkeypatch):
    import threading
    seen = []
    monkeypatch.setattr(helper, "contacts", lambda q: seen.append(threading.current_thread()) or [])
    await run(make_ctx(cfg, conn), call("send_whatsapp", to="Zed", text="hi"))
    assert seen and seen[0] is not threading.main_thread()


def test_start_helper_trusts_the_system_ca(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(whatsapp, "HELPER", tmp_path / "wa" / "helper.mjs")
    (tmp_path / "wa" / "node_modules").mkdir(parents=True)
    monkeypatch.setattr(whatsapp.subprocess, "Popen", lambda cmd, **kw: seen.update(kw) or "p")
    monkeypatch.setenv("NODE_OPTIONS", "--max-old-space-size=512")
    assert whatsapp.start_helper(tmp_path) == "p"
    assert seen["env"]["NODE_OPTIONS"] == "--max-old-space-size=512 --use-system-ca"
    monkeypatch.delenv("NODE_OPTIONS")
    whatsapp.start_helper(tmp_path)
    assert seen["env"]["NODE_OPTIONS"] == "--use-system-ca"


async def test_whatsapp_state_missing_when_not_installed(cfg, conn, monkeypatch):
    from jarvis.app import Hub
    monkeypatch.setattr(whatsapp, "installed", lambda: False)
    assert (await Hub(cfg, conn, None, None).whatsapp_state())["state"] == "missing"


async def test_poller_announces_a_state_change(cfg, conn, monkeypatch):
    from jarvis.app import Hub
    seen, states = [], iter(["unlinked", "linked", "linked"])

    class C:
        def status(self):
            return {"state": next(states)}

        def replies(self, since):
            return []
    monkeypatch.setattr(whatsapp, "client", lambda d: C())
    hub = Hub(cfg, conn, None, None)

    async def grab(m):
        seen.append(m)
        raise asyncio.CancelledError
    hub.broadcast = grab
    with pytest.raises(asyncio.CancelledError):
        await hub.whatsapp_replies(interval=0)
    assert seen == [{"type": "whatsapp", "state": "linked", "qr": ""}]
