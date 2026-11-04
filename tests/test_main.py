import pytest

from jarvis import __main__ as m


@pytest.fixture
def env(monkeypatch, tmp_path):
    exe = tmp_path / "electron.exe"
    exe.write_text("")
    launched, codes, opened = [], [], []
    terminated, registered = [], []
    monkeypatch.setattr(m.atexit, "register", registered.append)

    class FakePopen:
        def __init__(self, cmd, **kw):
            launched.append(cmd)
            self.code = codes.pop(0)

        def wait(self):
            return self.code

        def terminate(self):
            terminated.append(self)

    monkeypatch.setattr(m, "ELECTRON", exe)
    monkeypatch.setattr(m, "wait_for_server", lambda: None)
    monkeypatch.setattr(m, "open_full_view", lambda: opened.append(1))
    FakePopen.terminated, FakePopen.registered = terminated, registered
    return FakePopen, launched, codes, opened


def test_crash_restarts_once_then_stops(env):
    popen, launched, codes, _ = env
    codes += [1, 1, 1]
    m.open_island(popen)
    assert len(launched) == 2


def test_crash_then_clean_exit_launches_twice(env):
    popen, launched, codes, _ = env
    codes += [1, 0]
    m.open_island(popen)
    assert len(launched) == 2


def test_clean_quit_is_not_restarted(env):
    popen, launched, codes, _ = env
    codes += [0]
    m.open_island(popen)
    assert len(launched) == 1


def test_missing_electron_opens_full_view_with_hint(env, monkeypatch, tmp_path, capsys):
    popen, launched, _, opened = env
    monkeypatch.setattr(m, "ELECTRON", tmp_path / "nope.exe")
    m.open_island(popen)
    assert launched == [] and opened == [1]
    assert "npm install --prefix desktop" in capsys.readouterr().out


def test_island_is_terminated_when_jarvis_exits(env):
    popen, launched, codes, _ = env
    codes += [0]
    m.open_island(popen)
    [stop] = popen.registered   # run by atexit, e.g. after Ctrl+C
    stop()
    assert len(popen.terminated) == 1


def test_second_crash_falls_back_to_full_view(env):
    popen, launched, codes, opened = env
    codes += [1, 1]
    m.open_island(popen)
    assert len(launched) == 2 and opened == [1]


def test_crash_then_clean_exit_does_not_open_full_view(env):
    popen, launched, codes, opened = env
    codes += [1, 0]
    m.open_island(popen)
    assert opened == []
