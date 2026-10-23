"""The island page in a real browser (Chromium via Playwright), driven by fake server messages.
Skipped unless playwright is installed: pip install -r requirements-dev.txt; playwright install chromium."""
import asyncio
import json
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
    warnings = []
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 560, "height": 660})
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.on("console", lambda m: m.type == "warning" and m.text.startswith("island: couldn't show")
                and warnings.append(m.text))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script("window.__rects = []; window.island = { rect: r => window.__rects.push(r) }")
        page.goto(f"http://127.0.0.1:{port}/island")
        page.wait_for_selector("body.st-idle")
        page.push, page.answers, page.warnings = push, answers, warnings
        yield page
        assert errors == []   # no console errors during the run
        assert page.warnings == [] or getattr(page, "expect_warnings", False)   # a handler threw
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


DROP_TEXT = """() => { const d = new DataTransfer(); d.setData("text/plain", "hello vault");
  document.dispatchEvent(new DragEvent("drop", { dataTransfer: d, bubbles: true, cancelable: true })); }"""


def test_a_drop_while_a_server_confirm_is_showing_is_ignored(island):
    island.push({"type": "confirm", "id": "c1", "text": "Close Notepad, sir?", "seconds": 60})
    island.wait_for_selector("#confirm.on")
    island.evaluate(DROP_TEXT)
    island.wait_for_timeout(300)
    assert "Notepad" in island.inner_text("#cmsg") and "vault" not in island.inner_text("#cmsg")


def test_a_drop_with_nothing_showing_asks_to_keep(island):
    island.evaluate(DROP_TEXT)
    island.wait_for_selector("#confirm.on")
    assert "vault" in island.inner_text("#cmsg")


def test_confirm_without_text_does_not_throw(island):
    island.push({"type": "confirm", "id": "c2"})
    island.wait_for_selector("#confirm.on")
    island.wait_for_timeout(200)   # the fixture asserts no "couldn't show" warning


def test_whatsapp_styling_follows_the_send_pattern_not_the_word(island):
    island.push({"type": "confirm", "id": "c3", "text": "Open the WhatsApp settings, sir?", "seconds": 60})
    island.wait_for_selector("#confirm.on")
    assert not island.eval_on_selector("#confirm .go", "e => e.classList.contains('wa')")
    island.push({"type": "confirm", "id": "c4", "text": "send WhatsApp to Rahul: 'late'", "seconds": 60})
    island.wait_for_function("() => document.querySelector('#confirm .go').classList.contains('wa')")


def _history(island, messages):
    island.push({"type": "history", "messages": messages, "route": "fast"})
    island.wait_for_selector("#thread .b", state="attached")


def _wa(result):
    return json.dumps([{"name": "send_whatsapp", "arguments": json.dumps({"to": "Rahul", "text": "late"}),
                        "result": result}])


def test_history_marks_a_send_failed_only_when_the_result_starts_with_error(island):
    ts = "2026-10-23T09:00:00"
    _history(island, [
        {"role": "assistant", "content": "a", "screen": "", "ts": ts, "route": "fast", "actions": _wa("Error: helper down")},
        {"role": "assistant", "content": "b", "screen": "", "ts": ts, "route": "fast", "actions": _wa("Message delivered to Rahul")},
        {"role": "assistant", "content": "c", "screen": "", "ts": ts, "route": "fast", "actions": _wa("I could not say it was sent")},
    ])
    assert island.eval_on_selector_all("#thread .wa", "es => es.map(e => e.classList.contains('failed'))")         == [True, False, False]


def test_history_shows_the_mic_for_voice_user_messages(island):
    ts = "2026-10-23T09:00:00"
    _history(island, [{"role": "user", "content": "hi", "screen": "", "ts": ts, "route": "voice", "actions": ""},
                      {"role": "user", "content": "yo", "screen": "", "ts": ts, "route": "", "actions": ""}])
    assert island.locator("#thread .b.me .voice").count() == 1


def test_one_failing_handler_does_not_stop_the_other(island):
    island.expect_warnings = True
    island.push({"type": "history", "messages": [{"role": "user", "content": "kept", "ts": "2026-10-23T09:00:00"}]})
    island.wait_for_selector("#thread .b.me", state="attached")
    island.push({"type": "glance", "items": [None]})   # the panel's glance handler throws on a null item
    island.wait_for_function("() => true")
    island.wait_for_timeout(200)
    assert any("glance" in w for w in island.warnings)
    island.push({"type": "state", "state": "listening"})   # the socket loop is still alive
    island.wait_for_selector("body.st-listening")


def test_the_page_reports_the_island_rect_to_electron(island):
    island.wait_for_function("() => window.__rects.length > 0")
    island.wait_for_timeout(1200)   # the width settles with the first caption fit
    r = island.evaluate("() => window.__rects[window.__rects.length - 1]")
    box = island.locator("#shell").bounding_box()
    assert abs(r["x"] - box["x"]) <= 2 and abs(r["w"] - box["width"]) <= 2 and r["h"] > 30
    island.click("#shell")
    island.wait_for_selector("#shell.open #panel.in")
    island.wait_for_timeout(900)
    r = island.evaluate("() => window.__rects[window.__rects.length - 1]")
    assert r["w"] == 440 and r["h"] == 600   # follows the layout when it opens
