import base64
from datetime import datetime

import pytest

from jarvis import db, google
from jarvis.tools import TOOLS, day, google_tools, run, undo
from tests.test_tools import call, make_ctx

NOW = datetime(2026, 10, 12, 7, 30)


class Req:
    def __init__(self, result, log, name):
        self.result, self.log, self.name = result, log, name

    def execute(self):
        self.log.append(self.name)
        return self.result


class FakeGmail:
    def __init__(self, log):
        self.log = log

    def users(self):
        return self

    def messages(self):
        return self

    def drafts(self):
        return self

    def list(self, userId, q, maxResults):
        self.log.append(("q", q))
        return Req({"messages": [{"id": "m1"}]}, self.log, "list")

    def get(self, userId, id, format, metadataHeaders=None):
        body = base64.urlsafe_b64encode(b"Lab moved to Friday.").decode()
        return Req({"id": id, "snippet": "Lab moved", "payload": {
            "headers": [{"name": "From", "value": "Prof <p@srm.edu>"},
                        {"name": "Subject", "value": "Lab update"},
                        {"name": "Date", "value": "Mon, 12 Oct 2026"}],
            "mimeType": "text/plain", "body": {"data": body}}}, self.log, "get")

    def create(self, userId, body):
        self.log.append(("draft", body))
        return Req({"id": "d1"}, self.log, "create")

    def delete(self, userId, id):
        return Req({}, self.log, ("delete", id))


class FakeCalendar:
    def __init__(self, log):
        self.log = log

    def events(self):
        return self

    def list(self, **kw):
        return Req({"items": [{"summary": "Club meeting", "start": {
            "dateTime": "2026-10-12T17:00:00+05:30"}}]}, self.log, "list")

    def insert(self, calendarId, body):
        self.log.append(("insert", body))
        return Req({"id": "e1"}, self.log, "insert")

    def delete(self, calendarId, eventId):
        return Req({}, self.log, ("delete", eventId))


class FakeDrive:
    def __init__(self, log):
        self.log = log

    def files(self):
        return self

    def list(self, **kw):
        self.log.append(("drive_q", kw["q"]))
        return Req({"files": [{"name": "Physics notes", "webViewLink": "https://drive/x",
                               "modifiedTime": "2026-10-10T10:00:00Z"}]}, self.log, "list")


@pytest.fixture
def connected(monkeypatch):
    log = []
    fakes = {"gmail": FakeGmail(log), "calendar": FakeCalendar(log), "drive": FakeDrive(log)}
    monkeypatch.setattr(google, "service", lambda name, version: fakes[name])
    monkeypatch.setattr(day, "now", lambda: NOW)
    return log


@pytest.fixture
def disconnected(monkeypatch):
    def nope(name, version):
        raise google.NotConnected(f"Google isn't connected yet; {google.HOW}")
    monkeypatch.setattr(google, "service", nope)


async def test_gmail_search_and_read(cfg, conn, connected):
    r = await run(make_ctx(cfg, conn), call("gmail_search", query="from:prof"))
    assert "[m1] Prof <p@srm.edu>: Lab update" in r and ("q", "from:prof") in connected
    r = await run(make_ctx(cfg, conn), call("gmail_read", message_id="m1"))
    assert "Lab moved to Friday." in r and r.startswith('<untrusted source="gmail_read">')


async def test_gmail_draft_confirms_and_undo_deletes(cfg, conn, connected):
    ctx = make_ctx(cfg, conn)
    r = await run(ctx, call("gmail_draft", to="p@srm.edu", subject="Lab", body="Noted."))
    assert ctx.asked == ["Save an email draft to p@srm.edu, sir?"] and "draft" in r.lower()
    raw = [x for x in connected if isinstance(x, tuple) and x[0] == "draft"][0][1]
    assert "p@srm.edu" in base64.urlsafe_b64decode(raw["message"]["raw"]).decode()
    undo(conn, db.recent_actions(conn)[0]["id"])
    assert ("delete", "d1") in connected


async def test_calendar_events_and_add_undo(cfg, conn, connected):
    r = await run(make_ctx(cfg, conn), call("calendar_events", days=1))
    assert "17:00 Club meeting" in r
    ctx = make_ctx(cfg, conn)
    await run(ctx, call("calendar_add", title="Study group", start="2026-10-12T18:00",
                        end="2026-10-12T19:00"))
    assert ctx.asked and "Study group" in ctx.asked[0]
    undo(conn, db.recent_actions(conn)[0]["id"])
    assert ("delete", "e1") in connected


async def test_drive_search_escapes_quotes(cfg, conn, connected):
    r = await run(make_ctx(cfg, conn), call("drive_search", query="o'neil notes"))
    assert "Physics notes" in r
    assert ("drive_q", "name contains 'o\\'neil notes' and trashed = false") in connected


async def test_not_connected_says_how(cfg, conn, disconnected):
    r = await run(make_ctx(cfg, conn), call("gmail_search", query="x"))
    assert r.startswith("Error") and "accounts connect google" in r
    assert "isn't connected" in google_tools.briefing_part("email")


async def test_briefing_parts_when_connected(cfg, conn, connected):
    assert "17:00 Club meeting" in google_tools.briefing_part("calendar")
    assert "Prof <p@srm.edu>: Lab update" in google_tools.briefing_part("email")


def test_revoked_token_means_reconnect(monkeypatch):
    from google.auth.exceptions import RefreshError

    class Creds:
        expired, refresh_token, valid = True, "r", False

        def refresh(self, request):
            raise RefreshError("invalid_grant")
    monkeypatch.setattr(google, "get_key", lambda name: '{"token": "x"}')
    monkeypatch.setattr(google, "_load", lambda raw: Creds())
    with pytest.raises(google.NotConnected, match="reconnect"):
        google.credentials()


def test_flags():
    for n in ("gmail_search", "gmail_read", "calendar_events", "drive_search"):
        assert TOOLS[n].readonly and TOOLS[n].untrusted
    for n in ("gmail_draft", "calendar_add"):
        assert TOOLS[n].confirm and not TOOLS[n].readonly



async def test_revoked_mid_session_says_reconnect(cfg, conn, monkeypatch):
    from google.auth.exceptions import RefreshError

    class Revoked:
        def __getattr__(self, name):
            return lambda *a, **k: self

        def execute(self):
            raise RefreshError("invalid_grant: Token has been expired or revoked.")
    monkeypatch.setattr(google, "service", lambda name, version: Revoked())
    r = await run(make_ctx(cfg, conn), call("gmail_search", query="x"))
    assert "reconnect" in r and "accounts connect google" in r
    assert "reconnect" in google_tools.briefing_part("email")


def test_token_saved_without_access_token_and_refresh_not_repeated(monkeypatch):
    saved, refreshed = {}, []

    class Creds:
        valid, refresh_token, expired, token = False, "r", False, None

        def refresh(self, request):
            refreshed.append(1)
            self.valid = True
    monkeypatch.setattr(google, "get_key", lambda name: '{"refresh_token": "r"}')
    monkeypatch.setattr(google, "set_key", lambda name, value: saved.update({name: value}))
    monkeypatch.setattr(google, "_load", lambda raw: Creds())
    monkeypatch.setattr(google, "_creds", None)
    google.credentials()
    google.credentials()
    assert refreshed == [1] and not saved          # nothing big written back to the vault
