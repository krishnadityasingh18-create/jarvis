import getpass
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn

from jarvis import db
from jarvis.app import create_app
from jarvis.config import get_key, load_config, set_key

HOST, PORT = "127.0.0.1", 8765
URL = f"http://{HOST}:{PORT}"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def keys_cli(args: list[str]) -> None:
    cfg = load_config()
    names = sorted({r.key for r in cfg.routes.values() if r.key})
    if args[:1] == ["set"] and len(args) == 2:
        set_key(args[1], getpass.getpass(f"{args[1]} API key (input hidden): ").strip())
        print(f"Saved '{args[1]}' to Windows Credential Manager.")
    elif args[:1] == ["list"]:
        for n in names:
            print(f"{n}: {'set' if get_key(n) else 'missing'}")
    else:
        print("usage: python -m jarvis keys set <name> | keys list   (names: " + ", ".join(names) + ")")


def accounts_cli(args: list[str]) -> None:
    import subprocess as sp

    from jarvis import google
    cfg = load_config()
    if args == ["connect", "google"]:
        google.connect(cfg.db_path.parent / "google_client.json")
        print("Google connected (token saved to Windows Credential Manager).")
    elif args == ["list"]:
        print(f"google: {'connected' if google.connected() else 'not connected'}")
        try:
            ok = sp.run(["gh", "auth", "status"], capture_output=True, text=True).returncode == 0
        except FileNotFoundError:
            ok = False
        print(f"github: {'connected (gh)' if ok else 'not connected (install gh, then gh auth login)'}")
    else:
        print("usage: python -m jarvis accounts connect google | accounts list")


def remote_cli(args: list[str]) -> None:
    import secrets
    import shutil
    import subprocess as sp
    if args == ["token"]:
        token = secrets.token_urlsafe(32)
        set_key("remote_token", token)
        print("New remote token (saved to Windows Credential Manager; the old one stops working).")
        print("Paste it into Vapi (Custom LLM -> API key) and n8n (Authorization: Bearer <token>):")
        print(token)
    elif args == ["tunnel"]:
        local = load_config().db_path.parent / "bin" / "cloudflared.exe"   # no-install copy
        exe = shutil.which("cloudflared") or (str(local) if local.exists() else None)
        if not exe:
            raise SystemExit("Install cloudflared first: winget install Cloudflare.cloudflared "
                             "(or put cloudflared.exe in data\\bin)")
        print("Starting a Cloudflare quick tunnel to JARVIS (Ctrl+C to stop). Use the "
              "https://....trycloudflare.com address it prints:")
        print("  Vapi Custom LLM URL:  <address>/v1      n8n webhook target: <address>/api/message")
        sp.run([exe, "tunnel", "--url", URL])
    else:
        print("usage: python -m jarvis remote token | remote tunnel")


def desk_windows() -> list[int]:
    """JARVIS windows already open (Edge app windows titled Jarvis)."""
    from jarvis.app import WINDOW_TITLE
    from jarvis.tools import windows
    return [w["hwnd"] for w in windows.list_top()
            if w["title"] == WINDOW_TITLE and Path(w["exe"]).stem.lower() == "msedge"]


def open_window() -> None:
    for _ in range(60):
        try:
            urllib.request.urlopen(URL, timeout=1)
            break
        except OSError:
            time.sleep(0.5)
    # a restart used to open another window each time; an open one reconnects by itself
    if mine := desk_windows():
        from jarvis.tools import windows
        windows._bring(mine[0])
        return
    subprocess.Popen([EDGE, f"--app={URL}"])


def hook_hotkey(voice, key: str) -> None:
    import keyboard
    held = False

    def down(_):
        nonlocal held
        if not held:  # ignore key-repeat while held
            held = True
            voice.event("hotkey_down")

    def up(_):
        nonlocal held
        held = False
        voice.event("hotkey_up")

    keyboard.on_press_key(key, down)
    keyboard.on_release_key(key, up)


def main() -> None:
    if sys.argv[1:2] == ["remote"]:
        return remote_cli(sys.argv[2:])
    if sys.argv[1:2] == ["accounts"]:
        return accounts_cli(sys.argv[2:])
    if sys.argv[1:2] == ["keys"]:
        return keys_cli(sys.argv[2:])
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    for r in cfg.routes.values():
        if r.key and not get_key(r.key):
            print(f"[jarvis] route '{r.name}' has no key; run: python -m jarvis keys set {r.key}")

    from jarvis.voice import VoiceLoop
    voice = VoiceLoop(cfg, lambda n, p: None)
    app = create_app(cfg, conn, voice)
    hub = app.state.hub

    def on_event(name, payload):
        if name == "banner":
            print("[jarvis]", payload["text"])
        hub.from_thread(name, payload)

    voice.on_event = on_event
    from jarvis.tools import files
    files.start_indexer(cfg, lambda text: on_event("banner", {"text": text}))
    voice.start()
    hook_hotkey(voice, cfg.ptt_key)
    threading.Thread(target=open_window, daemon=True).start()
    print(f"[jarvis] {URL}  (push-to-talk: {cfg.ptt_key}; Ctrl+C to quit)")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
