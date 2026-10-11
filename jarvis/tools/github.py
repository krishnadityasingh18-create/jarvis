"""GitHub through the user's logged-in `gh` CLI (no new login or token)."""
import json
import subprocess

from jarvis.tools import tool

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _gh(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=30, creationflags=NO_WINDOW)
    if r.returncode:
        raise ValueError(r.stderr.strip()[:200] or "gh failed")
    return r.stdout.strip()


def _search(kind: str, flag: str) -> list[str]:
    items = json.loads(_gh("search", kind, flag, "--state=open", "--limit", "10",
                           "--json", "title,repository,url") or "[]")
    return [f"- {i['repository']['nameWithOwner']}: {i['title']}" for i in items]


def summary() -> str:
    parts = []
    for label, kind, flag in (("Review requests", "prs", "--review-requested=@me"),
                              ("Your open pull requests", "prs", "--author=@me"),
                              ("Issues assigned to you", "issues", "--assignee=@me")):
        rows = _search(kind, flag)
        if rows:
            parts.append(f"{label}:\n" + "\n".join(rows))
    unread = _gh("api", "notifications", "--jq", "length")
    parts.append(f"{unread or 0} unread notifications.")
    return "\n".join(parts)


@tool("The user's GitHub activity: review requests, open pull requests, assigned issues and "
      "unread notifications.", readonly=True, untrusted=True)
def github_activity(ctx):
    return summary(), None
