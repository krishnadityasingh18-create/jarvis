import pytest

from jarvis import db
from jarvis.tools import TOOLS, run, undo
from jarvis.tools import windows as w
from tests.test_tools import call, make_ctx

WINS = [{"hwnd": 1, "title": "Inbox - Google Chrome", "exe": "chrome"},
        {"hwnd": 2, "title": "YouTube - Google Chrome", "exe": "chrome"},
        {"hwnd": 3, "title": "notes.txt - Notepad", "exe": "notepad"}]


def test_find_window_unique_by_exe():
    assert w.find_window("notepad", WINS) == 3
    assert w.find_window("youtube", WINS) == 2


def test_find_window_ambiguous_returns_candidates():
    r = w.find_window("chrome", WINS)
    assert isinstance(r, list) and {x["hwnd"] for x in r} == {1, 2}


def test_find_window_none():
    assert w.find_window("photoshop", WINS) == []


def test_list_top_smoke():
    wins = w.list_top()
    assert isinstance(wins, list)
    assert all(isinstance(x["title"], str) and x["title"] for x in wins)


@pytest.fixture
def fake_win32(monkeypatch):
    log = []
    placement = [44, 0, 1, -1, -1, -1, -1, 10, 10, 500, 400]
    monkeypatch.setattr(w, "list_top", lambda: WINS)
    monkeypatch.setattr(w, "get_placement", lambda h: list(placement))
    monkeypatch.setattr(w, "set_placement", lambda h, d: log.append(("set", h, d)))
    monkeypatch.setattr(w, "_show", lambda h, cmd: log.append(("show", h, cmd)))
    monkeypatch.setattr(w, "_work_area", lambda h: (0, 0, 1920, 1040))
    monkeypatch.setattr(w, "_move", lambda h, x, y, cx, cy: log.append(("move", h, x, y, cx, cy)))
    monkeypatch.setattr(w, "_is_window", lambda h: True)
    return log, placement


async def test_snap_records_placement_undo(cfg, conn, fake_win32):
    log, placement = fake_win32
    r = await run(make_ctx(cfg, conn), call("snap_window", window="notepad", side="right"))
    assert "Snapped" in r and ("move", 3, 960, 0, 960, 1040) in log
    a = db.recent_actions(conn)[0]
    assert a["undo"] == {"kind": "placement", "hwnd": 3, "data": placement}
    undo(conn, a["id"])
    assert log[-1] == ("set", 3, placement)


async def test_ambiguous_window_does_nothing(cfg, conn, fake_win32):
    log, _ = fake_win32
    r = await run(make_ctx(cfg, conn), call("minimise_window", window="chrome"))
    assert "Inbox - Google Chrome" in r and "YouTube" in r and log == []


async def test_close_window_requires_confirm(cfg, conn, fake_win32, monkeypatch):
    closed = []
    monkeypatch.setattr(w, "close_hwnds", lambda hs: closed.extend(hs))
    ctx = make_ctx(cfg, conn, answer=False)
    await run(ctx, call("close_window", window="notepad"))
    assert TOOLS["close_window"].confirm and closed == []
    assert ctx.asked == ["Close notes.txt - Notepad, sir?"]
