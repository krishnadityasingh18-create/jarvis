"""The link to Claude Code: anything involving code goes to `claude -p` in a git project,
with the user's standing boundaries, and the result is committed (undo = git revert)."""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

from jarvis.tools import tool
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
with a clear message (git add -A; git commit). End with a 2-4 line plain summary of what
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


def _repo(project: str, cfg) -> Path:
    p = Path(project).expanduser()
    if not p.is_absolute() or not inside_roots(p, cfg.file_roots, cfg.file_exclude):
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


def _work(repo: Path, task: str, holder: dict) -> str:
    before = _head(repo)
    p = subprocess.Popen(
        _command() + ["-p", task, "--output-format", "json",
                      "--permission-mode", "bypassPermissions", "--append-system-prompt", RULES],
        cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    holder["proc"] = p
    try:
        out, err = p.communicate(timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        out, err = b"", b"timed out after 20 minutes"
    try:
        data = json.loads(out.decode("utf-8", errors="replace"))
        summary = str(data.get("result", "")).strip()
        failed = bool(data.get("is_error"))
    except ValueError:
        summary, failed = err.decode("utf-8", errors="replace").strip()[-500:], True
    if _git(repo, "status", "--porcelain"):   # Claude left changes uncommitted
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"JARVIS: {task[:60]}")
    after = _head(repo)
    commits = _git(repo, "log", "--oneline", f"{before}..{after}") if before and after != before \
        else (_git(repo, "log", "--oneline") if after and not before else "")
    head = "Claude Code reported a problem.\n" if failed else ""
    return head + (summary or "(no summary)") + (
        f"\n\nCommits in {repo.name}:\n{commits}" if commits else "\n\nNo changes were made."), \
        ({"kind": "git_revert", "repo": str(repo), "before": before, "after": after}
         if commits and before else None)


@tool("Hand any coding work to Claude Code: writing, changing, fixing, refactoring or "
      "explaining code, scripts, configs or programs. It works in a git project folder with "
      "full permissions inside the user's rules, commits its changes, and reports back.",
      exports=True,
      task="string: the complete request, in the user's words plus any detail they gave",
      project="string: full path of the project folder (a git repository)")
async def ask_claude(ctx, task, project):
    repo = _repo(project, ctx.cfg)
    await ctx.emit({"type": "agent", "agent": "Claude"})
    holder: dict = {}
    try:
        return await asyncio.to_thread(_work, repo, task, holder)
    except asyncio.CancelledError:
        if holder.get("proc"):
            holder["proc"].kill()   # the user moved on: stop Claude too
        raise


def undo(u: dict) -> None:
    repo = Path(u["repo"])
    if _git(repo, "status", "--porcelain"):
        raise ValueError(f"{repo.name} has uncommitted changes; I won't revert over them")
    _git(repo, "revert", "--no-edit", f"{u['before']}..{u['after']}")


KINDS = {"git_revert"}
