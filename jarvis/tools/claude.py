"""The link to Claude Code: anything involving code goes to `claude -p` in a git project,
with the user's standing boundaries, and the result is committed (undo = git revert)."""
import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from jarvis.tools import MAX_RESULT, _record, tool
from jarvis.tools.apps import hidden, inside_roots

TIMEOUT_S = 20 * 60
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# The boundaries the user set for this work. Claude can't ask questions here, so anything
# outside them is left undone and reported.
RULES = """You were called by JARVIS, the user's voice assistant, on the user's own request.
You have full permissions, inside these boundaries the user set:
- Never spend money or make any payment.
- Nothing permanent: never rewrite git history (no reset --hard, rebase, amend, force,
  branch -D, clean -f), never delete files that git doesn't track.
- Nothing outward-facing: never push, publish, deploy, open pull requests, send email or
  messages, or post anything online.
- Never put secrets (keys, passwords, tokens) in code.
- You can't ask the user anything. If part of the task needs something outside these
  boundaries or is unclear, do the safe part and say exactly what is left.
When you are done, run the project's tests if it has any, then commit all your changes
with a clear message (git add -A; git commit). Never commit build artifacts or caches
(__pycache__, *.pyc, node_modules, build output): add a .gitignore for them if needed. End with a 2-4 line plain summary of what
you changed, for JARVIS to read aloud."""


def _command() -> list[str]:
    exe = shutil.which("claude")
    if not exe:
        raise ValueError("Claude Code isn't installed (the `claude` command wasn't found)")
    return [exe]


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    if r.returncode:
        raise ValueError(f"git {args[0]} failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def _repos(cfg, max_depth: int = 4) -> list[Path]:
    """Git projects under the roots (not in hidden or excluded folders)."""
    found, skip = [], set(cfg.file_exclude)
    for root in cfg.file_roots:
        root = Path(root)
        for dirpath, dirnames, _ in os.walk(root):
            d = Path(dirpath)
            if (d / ".git").exists() and d != root:
                found.append(d)
            if len(d.relative_to(root).parts) >= max_depth:
                dirnames[:] = []
            dirnames[:] = [n for n in dirnames if n not in skip and not n.startswith(".")]
    return found


def _repo(project: str, cfg) -> Path:
    p = Path(project).expanduser()
    # A name, or a guessed path (even one the model just created): find the project by name.
    if not (p.is_absolute() and (p / ".git").exists()):
        repos = _repos(cfg)
        hits = [r for r in repos if r.name.lower() == p.name.lower()]
        if len(hits) != 1:
            names = ", ".join(sorted({r.name for r in (hits or repos)}))
            raise ValueError((f"several projects are called {p.name}: " if hits else
                              f"no git project called {p.name}; projects: ") + names)
        p = hits[0]
    if not inside_roots(p, cfg.file_roots, cfg.file_exclude):
        raise ValueError("that project is outside the folders I may use")
    p = p.resolve()
    if hidden(p, cfg.file_roots):
        raise ValueError("I don't send hidden folders to Claude")
    if not p.is_dir():
        raise ValueError("that project folder doesn't exist")
    if not (p / ".git").exists():
        raise ValueError("that folder isn't a git repository, so its changes couldn't be "
                         "committed or undone; ask the user to `git init` it first")
    return p


def _head(repo: Path) -> str:
    try:
        return _git(repo, "rev-parse", "HEAD")
    except ValueError:
        return ""   # a repository with no commits yet


def _run(cwd: Path, prompt: str, holder: dict) -> tuple[str, bool, str, dict | None]:
    """Run `claude -p` in cwd. Returns (Claude's exact reply, failed, commits, undo).
    In a git project, changes Claude left uncommitted are committed too."""
    repo = (cwd / ".git").exists()
    before = _head(cwd) if repo else ""
    p = subprocess.Popen(
        _command() + ["-p", prompt, "--output-format", "json",
                      "--permission-mode", "bypassPermissions", "--append-system-prompt", RULES],
        cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    holder["proc"] = p
    try:
        out, err = p.communicate(timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        out, err = b"", b"timed out after 20 minutes"
    try:
        data = json.loads(out.decode("utf-8", errors="replace"))
        reply, failed = str(data.get("result", "")), bool(data.get("is_error"))
    except ValueError:
        reply, failed = err.decode("utf-8", errors="replace").strip()[-500:], True
    if not repo:
        return reply, failed, "", None
    if _git(cwd, "status", "--porcelain"):   # Claude left changes uncommitted
        _git(cwd, "add", "-A")
        _git(cwd, "commit", "-q", "-m", f"JARVIS: {prompt[:60]}")
    after = _head(cwd)
    commits = (_git(cwd, "log", "--oneline", f"{before}..{after}") if before and after != before
               else _git(cwd, "log", "--oneline") if after and not before else "")
    undo_info = ({"kind": "git_revert", "repo": str(cwd), "before": before, "after": after}
                 if commits and before else None)
    return reply, failed, commits, undo_info


async def _run_async(cwd: Path, prompt: str):
    holder: dict = {}
    try:
        return await asyncio.to_thread(_run, cwd, prompt, holder)
    except asyncio.CancelledError:
        if holder.get("proc"):
            holder["proc"].kill()   # the user moved on: stop Claude too
        raise


@tool("Hand any coding work to Claude Code: writing, changing, fixing, refactoring or "
      "explaining code, scripts, configs or programs. It works in a git project folder with "
      "full permissions inside the user's rules, commits its changes, and reports back.",
      exports=True,
      task="string: the complete request, in the user's words plus any detail they gave",
      project="string: the project folder NAME as the user said it "
              "(e.g. motor-fault-detection); never guess a path")
async def ask_claude(ctx, task, project):
    repo = _repo(project, ctx.cfg)
    await ctx.emit({"type": "agent", "agent": "Claude"})
    reply, failed, commits, undo_info = await _run_async(repo, task)
    head = "Claude Code reported a problem.\n" if failed else ""
    return head + (reply.strip() or "(no summary)") + (
        f"\n\nCommits in {repo.name}:\n{commits}" if commits else "\n\nNo changes were made."), \
        undo_info


async def direct(ctx, prompt: str) -> str:
    """The user's own "ask Claude ..." line: the prompt goes exactly as said, and Claude's
    exact reply comes back. Runs in a project the prompt names, else a neutral workspace."""
    named = [r for r in _repos(ctx.cfg)
             if re.search(rf"(?<![\w-]){re.escape(r.name)}(?![\w-])", prompt, re.IGNORECASE)]
    if len(named) == 1:
        cwd = named[0]
    else:
        cwd = (ctx.cfg.db_path.parent / "claude").resolve()
        cwd.mkdir(parents=True, exist_ok=True)
    await ctx.emit({"type": "agent", "agent": "Claude"})
    reply, failed, commits, undo_info = await _run_async(cwd, prompt)
    note = f"\n\nCommits in {cwd.name}:\n{commits}" if commits else ""
    await ctx.emit(_record(ctx, "ask_claude", {"task": prompt, "project": cwd.name},
                           (reply + note)[:MAX_RESULT], "failed" if failed else "done",
                           undo_info))
    return reply


def undo(u: dict) -> None:
    repo = Path(u["repo"])
    if _git(repo, "status", "--porcelain"):
        raise ValueError(f"{repo.name} has uncommitted changes; I won't revert over them")
    _git(repo, "revert", "--no-edit", f"{u['before']}..{u['after']}")


KINDS = {"git_revert"}
