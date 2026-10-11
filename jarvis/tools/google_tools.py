"""Gmail (read, drafts), Calendar (read, add) and Drive (search) on the user's account."""
import base64
import functools
from datetime import datetime, timedelta
from email.message import EmailMessage

from jarvis import google
from jarvis.tools import day, tool

READ_CHARS = 6000


def _reconnect(fn):
    """Turn a sign-in revoked mid-session into the plain reconnect message."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except google.NotConnected:
            raise
        except Exception as e:
            if google.revoked(e):
                raise google.NotConnected(google.RECONNECT) from None
            raise
    return wrapper


def _header(msg: dict, name: str) -> str:
    return next((h["value"] for h in msg["payload"].get("headers", [])
                 if h["name"].lower() == name.lower()), "")


def _messages(query: str, n: int) -> list[str]:
    g = google.service("gmail", "v1")
    ids = g.users().messages().list(userId="me", q=query, maxResults=n).execute()
    out = []
    for m in ids.get("messages", []):
        msg = g.users().messages().get(userId="me", id=m["id"], format="metadata",
                                       metadataHeaders=["From", "Subject", "Date"]).execute()
        out.append(f"- [{m['id']}] {_header(msg, 'From')}: {_header(msg, 'Subject')} "
                   f"({_header(msg, 'Date')})")
    return out


@tool("Search the user's Gmail (Gmail search syntax, e.g. 'is:unread from:prof'). Returns "
      "message ids, senders and subjects.", readonly=True, untrusted=True,
      query="string: Gmail search query", n="?integer: how many, at most 20")
@_reconnect
def gmail_search(ctx, query, n=10):
    rows = _messages(query, max(1, min(int(n), 20)))
    return "\n".join(rows) or f"No email matches '{query}'.", None


def _text(part: dict) -> str:
    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
    return "".join(_text(p) for p in part.get("parts", []))


@tool("Read one email by its id (from gmail_search).", readonly=True, untrusted=True,
      max_chars=READ_CHARS + 400, message_id="string: the message id")
@_reconnect
def gmail_read(ctx, message_id):
    g = google.service("gmail", "v1")
    msg = g.users().messages().get(userId="me", id=message_id, format="full").execute()
    body = _text(msg["payload"]) or msg.get("snippet", "")
    return (f"From: {_header(msg, 'From')}\nSubject: {_header(msg, 'Subject')}\n"
            f"Date: {_header(msg, 'Date')}\n\n{body[:READ_CHARS]}"), None


@tool("Save an email as a draft in the user's Gmail (it is NOT sent; the user sends it). "
      "Asks first.", confirm=True, exports=True,
      describe=lambda a: f"save an email draft to {a.get('to', '')}",
      to="string: recipient address", subject="string: subject", body="string: the text")
@_reconnect
def gmail_draft(ctx, to, subject, body):
    m = EmailMessage()
    m["To"], m["Subject"] = to, subject
    m.set_content(body)
    raw = base64.urlsafe_b64encode(m.as_bytes()).decode()
    d = google.service("gmail", "v1").users().drafts().create(
        userId="me", body={"message": {"raw": raw}}).execute()
    return f"Draft saved to Gmail (not sent): {subject}.", {"kind": "gmail_draft_delete",
                                                            "id": d["id"]}


def _events(days: int) -> list[str]:
    start = day.now().replace(hour=0, minute=0, second=0, microsecond=0).astimezone()
    items = google.service("calendar", "v3").events().list(
        calendarId="primary", timeMin=start.isoformat(),
        timeMax=(start + timedelta(days=days)).isoformat(), singleEvents=True,
        orderBy="startTime", maxResults=50).execute().get("items", [])
    out = []
    for e in items:
        s = e["start"].get("dateTime") or e["start"].get("date")
        when = datetime.fromisoformat(s).strftime("%a %H:%M" if days > 1 else "%H:%M") \
            if "T" in s else "all day"
        out.append(f"- {when} {e.get('summary', '(no title)')}")
    return out


@tool("List events on the user's Google Calendar.", readonly=True, untrusted=True,
      days="?integer: how many days from today, default 1")
@_reconnect
def calendar_events(ctx, days=1):
    return "\n".join(_events(max(1, min(int(days), 31)))) or "No events.", None


@tool("Add an event to the user's Google Calendar. Asks first.", confirm=True, exports=True,
      describe=lambda a: f"add '{a.get('title', '')}' to your calendar at {a.get('start', '')}",
      title="string: event title", start="string: YYYY-MM-DDTHH:MM local",
      end="string: YYYY-MM-DDTHH:MM local")
@_reconnect
def calendar_add(ctx, title, start, end):
    s, e = (datetime.fromisoformat(x).astimezone() for x in (start, end))
    if e <= s:
        raise ValueError("the event must end after it starts")
    ev = google.service("calendar", "v3").events().insert(calendarId="primary", body={
        "summary": title, "start": {"dateTime": s.isoformat()},
        "end": {"dateTime": e.isoformat()}}).execute()
    return f"Added '{title}' on {s:%a %d %b %H:%M}.", {"kind": "calendar_delete", "id": ev["id"]}


@tool("Search the user's Google Drive by file name.", readonly=True, untrusted=True,
      query="string: words in the file name", n="?integer: how many, at most 20")
@_reconnect
def drive_search(ctx, query, n=10):
    q = "name contains '" + query.replace("\\", "\\\\").replace("'", "\\'") + \
        "' and trashed = false"
    files = google.service("drive", "v3").files().list(
        q=q, pageSize=max(1, min(int(n), 20)), orderBy="modifiedTime desc",
        fields="files(name,webViewLink,modifiedTime)").execute().get("files", [])
    return "\n".join(f"- {f['name']} ({f['modifiedTime'][:10]}) {f.get('webViewLink', '')}"
                     for f in files) or f"No Drive files match '{query}'.", None


@_reconnect
def _briefing_part(kind: str) -> str:
    if kind == "calendar":
        return "\n".join(_events(1)) or "nothing today"
    return "\n".join(_messages("is:unread is:important newer_than:2d", 5)) or \
        "no unread important email"


def briefing_part(kind: str) -> str:
    try:
        return _briefing_part(kind)
    except google.NotConnected as e:
        return str(e)


def undo(u: dict) -> None:
    if u["kind"] == "gmail_draft_delete":
        google.service("gmail", "v1").users().drafts().delete(userId="me", id=u["id"]).execute()
    else:
        google.service("calendar", "v3").events().delete(calendarId="primary",
                                                         eventId=u["id"]).execute()


KINDS = {"gmail_draft_delete", "calendar_delete"}
