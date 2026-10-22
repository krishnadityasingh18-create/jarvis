"""The island page in a real browser (Chromium via Playwright), driven by fake server messages.
Skipped unless playwright is installed: pip install -r requirements-dev.txt; playwright install chromium."""
import asyncio
import socket
import threading
import time

import pytest

pw = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

from jarvis import app as app_mod  # noqa: E402
from jarvis import db  # noqa: E402


@pytest.fixture
def island(cfg, conn, monkeypatch):
    db.add_message(conn, "user", "What's next?")
    db.add_message(conn, "assistant", "Physics is at 9:00 in TP-201, sir.", "", "fast")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    monkeypatch.setattr(app_mod, "ALLOWED_ORIGINS", {f"http://127.0.0.1:{port}"})

    async def no_turn(*a, **k):
        pass

    app = app_mod.create_app(cfg, conn, voice=None, turn_runner=no_turn)
    hub = app.state.hub
    answers = []
    monkeypatch.setattr(hub, "_answer", lambda ok, cid=None: answers.append((ok, cid)))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(.05)

    def push(msg):
        asyncio.run_coroutine_threadsafe(hub.broadcast(msg), hub.loop).result(5)

    errors = []
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 560, "height": 660})
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/island")
        page.wait_for_selector("body.st-idle")
        page.push, page.answers = push, answers
        yield page
        assert errors == []   # no console errors during the run
        browser.close()
    server.should_exit = True
    thread.join(5)


@pytest.mark.parametrize("state", ["idle", "listening", "thinking", "speaking"])
def test_each_state_sets_the_body_class(island, state):
    if state == "idle":
        island.push({"type": "state", "state": "thinking"})
    island.push({"type": "state", "state": state})
    island.wait_for_selector(f"body.st-{state}")
    if state == "thinking":
        assert island.inner_text("#mid") == "Thinking"


def test_confirm_card_sends_the_answer(island):
    island.push({"type": "confirm", "id": "c1", "text": "send WhatsApp to Rahul: 'late'", "seconds": 60})
    island.wait_for_selector("#confirm.on")
    island.click("#confirm .go")
    island.wait_for_function("() => !document.querySelector('#confirm.on')")
    deadline = time.time() + 5
    while not island.answers and time.time() < deadline:
        time.sleep(.05)
    assert island.answers == [(True, "c1")]


def test_click_opens_the_panel_and_escape_closes_it(island):
    island.click("#shell")
    island.wait_for_selector("#shell.open #panel.in")
    island.keyboard.press("Escape")
    island.wait_for_function("() => !document.querySelector('#shell.open')")


def test_frame_budget_over_expand_collapse_and_peek(island):
    island.wait_for_timeout(1500)   # boot (the orb's layers are drawn once) is not part of the budget
    island.evaluate("""() => { window.__d = []; let l = performance.now();
      const f = t => { window.__d.push(t - l); l = t; if (!window.__stop) requestAnimationFrame(f); };
      requestAnimationFrame(f); }""")
    box = island.locator("#shell").bounding_box()
    for _ in range(10):
        island.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)   # peek
        island.wait_for_timeout(450)
        island.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)   # expand
        island.wait_for_timeout(700)
        island.keyboard.press("Escape")   # collapse
        island.mouse.move(5, 640)
        island.wait_for_timeout(600)
    deltas = island.evaluate("() => { window.__stop = true; return window.__d.slice(1); }")
    slow = sum(d > 20 for d in deltas)
    print(f"frame budget: {slow}/{len(deltas)} frames over 20 ms, worst {max(deltas):.1f} ms")
    assert len(deltas) > 300 and slow / len(deltas) <= .01, f"{slow} of {len(deltas)} frames over 20 ms"
