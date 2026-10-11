import json
import subprocess
import sys

import pytest

from jarvis import db
from jarvis.tools import TOOLS, claude, run, undo
from tests.test_tools import call, make_ctx

FAKE = r'''
import json, os, subprocess, sys
open(os.environ["ARGV_LOG"], "w").write(json.dumps(sys.argv[1:]))
open("hello.txt", "w").write("hi\n")
if os.environ.get("FAKE_COMMITS") == "1":
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", "Add hello"], check=True)
print(json.dumps({"result": "Added hello.txt with a greeting.", "is_error": False}))
'''


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture
def repo(cfg, tmp_path, monkeypatch):
    home = tmp_path / "home"
    r = home / "proj"
    r.mkdir(parents=True)
    git(r, "init", "-q")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "T")
    (r / "a.txt").write_text("a\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    cfg.file_roots = [home]
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE)
    monkeypatch.setattr(claude, "_command", lambda: [sys.executable, str(script)])
    monkeypatch.setenv("ARGV_LOG", str(tmp_path / "argv.json"))
    return r


async def test_runs_in_repo_and_commits_leftovers(cfg, conn, repo, tmp_path):
    ctx = make_ctx(cfg, conn)
    r = await run(ctx, call("ask_claude", task="add a hello file", project=str(repo)))
    assert ctx.asked == [] and "Added hello.txt" in r
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "log", "-1", "--format=%s") == "JARVIS: add a hello file"
    argv = json.loads((tmp_path / "argv.json").read_text())
    assert argv[0] == "-p" and "add a hello file" in argv[1]
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    rules = argv[argv.index("--append-system-prompt") + 1]
    assert "never push" in rules.lower() and "payment" in rules.lower()


async def test_claude_own_commit_is_kept(cfg, conn, repo, monkeypatch):
    monkeypatch.setenv("FAKE_COMMITS", "1")
    r = await run(make_ctx(cfg, conn), call("ask_claude", task="add hello", project=str(repo)))
    assert git(repo, "log", "-1", "--format=%s") == "Add hello" and "Add hello" in r


async def test_undo_reverts_and_keeps_history(cfg, conn, repo):
    await run(make_ctx(cfg, conn), call("ask_claude", task="add hello", project=str(repo)))
    commits = int(git(repo, "rev-list", "--count", "HEAD"))
    undo(conn, db.recent_actions(conn)[0]["id"])
    assert not (repo / "hello.txt").exists()
    assert int(git(repo, "rev-list", "--count", "HEAD")) == commits + 1     # a revert commit


@pytest.mark.parametrize("where", ["not_a_repo", "outside", "hidden"])
async def test_refuses_bad_projects(cfg, conn, repo, tmp_path, where):
    target = {"not_a_repo": repo.parent, "outside": tmp_path,
              "hidden": repo.parent / ".secret"}[where]
    target.mkdir(exist_ok=True)
    if where == "hidden":
        git(target, "init", "-q")
    r = await run(make_ctx(cfg, conn), call("ask_claude", task="x", project=str(target)))
    assert r.startswith("Error") and not (target / "hello.txt").exists()


async def test_tainted_turn_asks_first(cfg, conn, repo):
    ctx = make_ctx(cfg, conn, answer=False)
    ctx.tainted = True
    r = await run(ctx, call("ask_claude", task="x", project=str(repo)))
    assert r == "The user declined." and not (repo / "hello.txt").exists()


def test_flags():
    t = TOOLS["ask_claude"]
    assert not t.confirm and not t.readonly and t.exports


@pytest.mark.parametrize("name", ["tool.py", "page.html", "conf.json", "run.sh", "Makefile",
                                  "main.c"])
async def test_jarvis_writes_no_code(cfg, conn, tmp_path, name):
    cfg.file_roots = [tmp_path]
    r = await run(make_ctx(cfg, conn), call("create_file", path=str(tmp_path / name),
                                            content="x"))
    assert r.startswith("Error") and "ask_claude" in r and not (tmp_path / name).exists()


async def test_jarvis_still_writes_notes(cfg, conn, tmp_path):
    cfg.file_roots = [tmp_path]
    r = await run(make_ctx(cfg, conn), call("create_file", path=str(tmp_path / "todo.md"),
                                            content="x"))
    assert r.startswith("Created")


async def test_project_found_by_name(cfg, conn, repo):
    r = await run(make_ctx(cfg, conn), call("ask_claude", task="add hello", project="PROJ"))
    assert "Added hello.txt" in r and (repo / "hello.txt").exists()


async def test_wrong_path_falls_back_to_the_name(cfg, conn, repo):
    wrong = str(repo.parent / "OneDrive" / "Documents" / "proj")
    r = await run(make_ctx(cfg, conn), call("ask_claude", task="add hello", project=wrong))
    assert (repo / "hello.txt").exists(), r


async def test_unknown_project_lists_candidates(cfg, conn, repo):
    r = await run(make_ctx(cfg, conn), call("ask_claude", task="x", project="nonexistent"))
    assert r.startswith("Error") and "proj" in r
