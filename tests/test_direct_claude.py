import json
import subprocess
import sys

import pytest

from jarvis import brain, db
from jarvis.tools import claude
from tests.test_brain import noop_emit
from tests.test_claude import git

ANSWER = "4\n\n```python\nprint(2 + 2)\n```"
FAKE = r'''
import json, os, sys
open(os.environ["ARGV_LOG"], "w").write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}))
if os.environ.get("FAKE_WRITE") == "1":
    open("hello.txt", "w").write("hi\n")
print(json.dumps({"result": os.environ.get("FAKE_RESULT", "4"), "is_error": False}))
'''


@pytest.fixture
def fake(cfg, tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = home / "proj"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "T")
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    cfg.file_roots = [home]
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE)
    monkeypatch.setattr(claude, "_command", lambda: [sys.executable, str(script)])
    log = tmp_path / "argv.json"
    monkeypatch.setenv("ARGV_LOG", str(log))
    monkeypatch.setattr(brain, "stream_reply", lambda *a, **k: pytest.fail("LLM was used"))
    return repo, lambda: json.loads(log.read_text())


async def turn(cfg, conn, text):
    said, events = [], []

    async def speak(s):
        said.append(s)

    async def emit(m):
        events.append(m)
    await brain.run_turn(cfg, conn, text, "fast", emit, speak)
    return said, events


@pytest.mark.parametrize("text,prompt", [
    ("ask Claude what is 2+2?", "what is 2+2?"),
    ("Hey Jarvis, ask Claude: what is 2+2?", "what is 2+2?"),
    ("Ask cloud, what is 2+2?", "what is 2+2?"),
    ("ASK CLAUDE to explain recursion", "to explain recursion"),
])
def test_direct_prefix(text, prompt):
    assert brain.direct_claude_prompt(text) == prompt


def test_not_direct():
    assert brain.direct_claude_prompt("can you ask claude later") is None
    assert brain.direct_claude_prompt("what is claude") is None


async def test_short_answer_spoken_and_shown_exactly(cfg, conn, fake):
    said, events = await turn(cfg, conn, "ask Claude what is 2+2?")
    seen = fake[1]()
    assert seen["argv"][:2] == ["-p", "what is 2+2?"]                 # prompt sent as said
    assert said == ["4"]
    assert db.recent_messages(conn)[-1]["content"] == "4"
    assert {"type": "agent", "agent": "Claude"} in events and events[-1]["type"] == "turn_end"


async def test_long_or_code_answer_shown_exactly(cfg, conn, fake, monkeypatch):
    monkeypatch.setenv("FAKE_RESULT", ANSWER)
    said, events = await turn(cfg, conn, "ask Claude how do I print 2+2 in python")
    screen = "".join(e["text"] for e in events if e["type"] == "screen_delta")
    assert screen == ANSWER and said == [brain.ON_SCREEN]
    last = db.recent_messages(conn)[-1]
    assert last["screen"] == ANSWER


async def test_named_project_runs_there_and_commits(cfg, conn, fake, monkeypatch):
    repo, seen = fake
    monkeypatch.setenv("FAKE_WRITE", "1")
    await turn(cfg, conn, "ask Claude to add a hello file to proj")
    assert seen()["cwd"] == str(repo)
    assert git(repo, "status", "--porcelain") == "" and (repo / "hello.txt").exists()
    a = db.recent_actions(conn)[0]
    assert a["tool"] == "ask_claude" and a["undo"]["kind"] == "git_revert"


async def test_no_project_runs_in_workspace(cfg, conn, fake):
    await turn(cfg, conn, "ask Claude what is 2+2?")
    assert fake[1]()["cwd"] == str((cfg.db_path.parent / "claude").resolve())
