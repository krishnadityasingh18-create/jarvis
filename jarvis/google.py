"""Google sign-in: the user's own OAuth client, browser consent, token in Credential Manager."""
import json
from pathlib import Path

from jarvis.config import get_key, set_key

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.compose",
          "https://www.googleapis.com/auth/calendar.events",
          "https://www.googleapis.com/auth/drive.readonly"]
TOKEN = "google_token"
HOW = "the user can run: python -m jarvis accounts connect google"


class NotConnected(Exception):
    pass


def connect(client_file: Path) -> None:
    """One-time browser consent with the user's own OAuth client (Desktop app)."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not client_file.exists():
        raise SystemExit(f"Put your Google OAuth client file at {client_file} first "
                         "(see README: Connecting Google).")
    creds = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES) \
        .run_local_server(port=0, open_browser=True)
    set_key(TOKEN, creds.to_json())


def _load(raw: str):
    from google.oauth2.credentials import Credentials
    return Credentials.from_authorized_user_info(json.loads(raw), SCOPES)


def credentials():
    raw = get_key(TOKEN)
    if not raw:
        raise NotConnected(f"Google isn't connected yet; {HOW}")
    creds = _load(raw)
    if not creds.valid and creds.expired and creds.refresh_token:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        try:
            creds.refresh(Request())
        except RefreshError:
            raise NotConnected(f"Google needs you to reconnect (the sign-in expired or was "
                               f"revoked); {HOW}") from None
        set_key(TOKEN, creds.to_json())
    return creds


def service(name: str, version: str):
    from googleapiclient.discovery import build
    return build(name, version, credentials=credentials(), cache_discovery=False)


def connected() -> bool:
    return bool(get_key(TOKEN))
