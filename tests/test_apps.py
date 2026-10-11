import os

import pytest

from jarvis import db
from jarvis.tools import TOOLS, apps, run
from tests.test_tools import call, make_ctx

FAKE_APPS = {"Google Chrome": "Chrome", "Notepad": "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App",
             "Visual Studio Code": "Microsoft.VisualStudioCode"}


@pytest.fixture
def opened(monkeypatch):
    log = []
    monkeypatch.setattr(os, "startfile", lambda p: log.append(str(p)), raising=False)
    monkeypatch.setattr(apps, "new_windows_after", lambda fn, wait=3.0: (fn(), [42])[1])
    from jarvis.tools import windows
    monkeypatch.setattr(windows, "_exe", lambda h: "chrome")
    return log


@pytest.fixture
def roots(cfg, tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "motor.pdf").write_text("x")
    (tmp_path / "notes" / "run.bat").write_text("x")
    (tmp_path / "notes" / "go.lnk").write_text("x")
    cfg.file_roots = [tmp_path / "notes"]
    return tmp_path


def test_match_app_fuzzy():
    assert apps.match_app("chrome", FAKE_APPS) == "Chrome"
    assert apps.match_app("notpad", FAKE_APPS) == FAKE_APPS["Notepad"]
    assert apps.match_app("code", FAKE_APPS) == "Microsoft.VisualStudioCode"
    assert apps.match_app("xyz", FAKE_APPS) is None


async def test_open_app_launches_and_records_undo(cfg, conn, opened, monkeypatch):
    monkeypatch.setattr(apps, "APPS", dict(FAKE_APPS))
    r = await run(make_ctx(cfg, conn), call("open_app", name="chrome"))
    assert r == "Opened Google Chrome." and opened == ["shell:AppsFolder\\Chrome"]
    assert db.recent_actions(conn)[0]["undo"] == {"kind": "close_windows", "hwnds": [42],
                                                  "exes": ["chrome"]}


async def test_open_app_unknown_suggests(cfg, conn, opened, monkeypatch):
    monkeypatch.setattr(apps, "APPS", dict(FAKE_APPS))
    r = await run(make_ctx(cfg, conn), call("open_app", name="photoshop"))
    assert r.startswith("Error") and opened == []


async def test_open_path_opens_inside_roots(cfg, conn, opened, roots):
    r = await run(make_ctx(cfg, conn), call("open_path", path=str(roots / "notes" / "motor.pdf")))
    assert r.startswith("Opened") and opened == [str((roots / "notes" / "motor.pdf").resolve())]


async def test_open_path_refuses_outside_roots_and_traversal(cfg, conn, opened, roots):
    (roots / "secret.txt").write_text("x")
    for p in (roots / "secret.txt", roots / "notes" / ".." / "secret.txt"):
        r = await run(make_ctx(cfg, conn), call("open_path", path=str(p)))
        assert "outside" in r
    assert opened == []


async def test_open_path_refuses_executables(cfg, conn, opened, roots):
    for f in ("run.bat", "go.lnk"):
        r = await run(make_ctx(cfg, conn), call("open_path", path=str(roots / "notes" / f)))
        assert "run_script" in r
    assert opened == []


async def test_open_path_refuses_excluded_dirs(cfg, conn, opened, roots):
    (roots / "notes" / "node_modules").mkdir()
    (roots / "notes" / "node_modules" / "a.txt").write_text("x")
    r = await run(make_ctx(cfg, conn), call("open_path",
                                             path=str(roots / "notes" / "node_modules" / "a.txt")))
    assert "outside" in r and opened == []


async def test_run_script_py(cfg, conn, tmp_path, monkeypatch):
    monkeypatch.setattr(apps, "SCRIPTS_DIR", tmp_path)
    (tmp_path / "hi.py").write_text("print('hi there')")
    r = await run(make_ctx(cfg, conn), call("run_script", name="hi"))
    assert "exit 0" in r and "hi there" in r
    assert TOOLS["run_script"].confirm


async def test_run_script_unknown_lists_available(cfg, conn, tmp_path, monkeypatch):
    monkeypatch.setattr(apps, "SCRIPTS_DIR", tmp_path)
    (tmp_path / "backup.ps1").write_text("")
    r = await run(make_ctx(cfg, conn), call("run_script", name="nuke"))
    assert r.startswith("Error") and "backup.ps1" in r


async def test_open_app_fallback_refuses_paths_and_programs(cfg, conn, opened, monkeypatch):
    monkeypatch.setattr(apps, "APPS", {})
    for name in ("C:\\Users\\x\\Downloads\\x.exe", "\\\\server\\share",
                 "https://evil.example", "payload.bat"):
        r = await run(make_ctx(cfg, conn), call("open_app", name=name))
        assert r.startswith("Error"), name
    assert opened == []
    await run(make_ctx(cfg, conn), call("open_app", name="notepad"))
    assert opened == ["notepad"]


async def test_open_path_refuses_dangerous_types_windows_knows(cfg, conn, opened, roots):
    (roots / "notes" / "help.chm").write_text("x")
    r = await run(make_ctx(cfg, conn), call("open_path", path=str(roots / "notes" / "help.chm")))
    assert "run_script" in r and opened == []
