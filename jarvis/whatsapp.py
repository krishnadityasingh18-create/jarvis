"""Client for the local WhatsApp helper (desktop/wa/helper.mjs): the user's own number,
linked once by QR. The helper listens on 127.0.0.1 with a token kept in <data>/wa/port."""
import json
import subprocess
import time
from pathlib import Path

import httpx

HOURLY_CAP = 20
HELPER = Path(__file__).resolve().parents[1] / "desktop" / "wa" / "helper.mjs"


class Client:
    def __init__(self, port: int, token: str):
        self.base, self.headers = f"http://127.0.0.1:{port}", {"Authorization": f"Bearer {token}"}

    def _req(self, method: str, path: str, timeout: float = 5, **kw) -> dict:
        r = httpx.request(method, self.base + path, headers=self.headers, timeout=timeout, **kw)
        r.raise_for_status()
        return r.json()

    def status(self) -> dict | None:
        """{"state","qr"[,"qr_text"]}, or None if the helper doesn't answer."""
        try:
            return self._req("GET", "/status")
        except Exception:
            return None

    def contacts(self, q: str) -> list[dict]:
        return self._req("GET", "/contacts", params={"q": q})["matches"]

    def send(self, number: str, text: str) -> dict:
        """{"ok":True,"id"} only after WhatsApp's server acknowledged it; any failure
        (helper down or dying mid-send included) is {"ok":False,"error"}."""
        try:
            return self._req("POST", "/send", timeout=30, json={"to": number, "text": text})
        except Exception as e:
            return {"ok": False, "error": f"the WhatsApp helper didn't answer ({type(e).__name__})"}

    def replies(self, since: str) -> list[dict]:
        try:
            return self._req("GET", "/replies", params={"since": since})["messages"]
        except Exception:
            return []


def client(data_dir: Path) -> Client | None:
    """The helper's client, or None if it isn't running (no port file, or no answer)."""
    try:
        info = json.loads((Path(data_dir) / "wa" / "port").read_text())
        c = Client(int(info["port"]), info["token"])
    except (OSError, ValueError, KeyError):
        return None
    return c if c.status() is not None else None


def start_helper(data_dir: Path) -> subprocess.Popen | None:
    """Start the helper (needs node and `npm install --prefix desktop/wa`); None if it can't."""
    data_dir = Path(data_dir)
    if not (HELPER.parent / "node_modules").exists():
        return None
    (data_dir / "wa" / "port").unlink(missing_ok=True)   # a stale port from a crashed run
    try:
        return subprocess.Popen(["node", str(HELPER), "--data", str(data_dir)],
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        return None


def wait_for(data_dir: Path, seconds: float = 20) -> Client | None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if c := client(data_dir):
            return c
        time.sleep(0.5)
    return None
