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
    # Credential Manager holds ~1280 characters: keep only what's needed to refresh
    set_key(TOKEN, creds.to_json(strip=["token", "expiry"]))


def _load(raw: str):
    from google.oauth2.credentials import Credentials
    return Credentials.from_authorized_user_info(json.loads(raw), SCOPES)


RECONNECT = f"Google needs you to reconnect (the sign-in expired or was revoked); {HOW}"
_creds = None   # refreshed credentials, kept in memory (the vault holds only the refresh token)


def credentials():
    global _creds
    if _creds is not None and _creds.valid:
        return _creds
    raw = get_key(TOKEN)
    if not raw:
        raise NotConnected(f"Google isn't connected yet; {HOW}")
    creds = _load(raw)
    if not creds.valid and creds.refresh_token:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        try:
            creds.refresh(Request())
        except RefreshError:
            raise NotConnected(RECONNECT) from None
    _creds = creds
    return creds


def revoked(e: Exception) -> bool:
    """A refresh failure or HTTP 401 from any Google call: the sign-in is no longer valid."""
    from google.auth.exceptions import RefreshError
    status = getattr(getattr(e, "resp", None), "status", None)
    return isinstance(e, RefreshError) or status == 401


def service(name: str, version: str):
    from googleapiclient.discovery import build
    return build(name, version, credentials=credentials(), cache_discovery=False)


def connected() -> bool:
    return bool(get_key(TOKEN))
