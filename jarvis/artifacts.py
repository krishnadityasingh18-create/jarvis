"""What JARVIS has made (files, saved answers, commits) and the island's glance data."""
import json
import re
import time
from datetime import timedelta
from pathlib import Path

from jarvis import db
from jarvis.tools import briefing, day, tool

FILE_TOOLS = ("create_file", "write_file", "edit_file")
RESEARCH_TOOLS = {"web_search", "fetch_page", "delegate"}
SAVED = re.compile(r"Saved (document|research): (.+)")


def _preview(p: Path) -> str:
    try:
        with open(p, encoding="utf-8") as f:
            return f.read(300)
    except (OSError, UnicodeDecodeError):
        return ""


def _exists(path: str, kind: str = "file") -> bool:
    p = Path(path)   # never resolve a bare name against the cwd
    return p.is_absolute() and (p.is_dir() if kind == "commit" else p.exists())


def _entry(a: dict) -> tuple[str, str, str, str] | None:
    """(kind, title, path, commit message) for one finished action, or None if it made nothing."""
    tool_, args = a["tool"], a["args"]
    if tool_ in FILE_TOOLS and args.get("path"):
        return "file", Path(args["path"]).name, str(args["path"]), ""
    if tool_ == "save_answer" and (m := SAVED.fullmatch(a["result"].strip())):
        return m[1], args.get("title") or Path(m[2]).stem, m[2], ""
    if tool_ == "ask_claude" and "Commits in " in a["result"]:
        repo = (a["undo"] or {}).get("repo") or args.get("project", "")
        first = a["result"].split("Commits in ", 1)[1].split("\n")[1:2]
        return "commit", Path(repo).name, repo, first[0].split(" ", 1)[-1] if first else ""
    return None


def _rows(conn):
    marks = ",".join("?" * (len(FILE_TOOLS) + 2))
    return conn.execute(f"SELECT * FROM actions WHERE status='done' AND tool IN ({marks}) "
                        "ORDER BY id DESC", (*FILE_TOOLS, "save_answer", "ask_claude"))


def items(conn, n: int = 24) -> list[dict]:
    """The `artifacts` socket message items, newest first, one per path."""
    out, seen = [], set()
    for r in _rows(conn):
        a = db._action(r)
        e = _entry(a)
        if not e or e[2] in seen:
            continue
        seen.add(e[2])
        # previews are read only for the rows that make the cut
        out.append({"id": a["id"], "kind": e[0], "title": e[1], "path": e[2], "preview": e[3],
                    "ts": a["ts"], "exists": _exists(e[2], e[0])})
        if len(out) == n:
            break
    for it in out:
        if it["kind"] != "commit":
            it["preview"] = _preview(Path(it["path"])) if it["exists"] else ""
    return out


def get(conn, item_id: int) -> dict | None:
    """One artifact by action id (no preview); None if it isn't an artifact."""
    r = conn.execute("SELECT * FROM actions WHERE id=? AND status='done'", (item_id,)).fetchone()
    e = r and _entry(db._action(r))
    return e and {"id": item_id, "kind": e[0], "title": e[1], "path": e[2], "exists": _exists(e[2], e[0])}


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50] or "answer"


@tool(local_only=True, description="Save the last answer as a document the user can open again "
      "(use for 'save that' / 'keep that answer as a doc').", title="string: a short title")
def save_answer(ctx, title):
    row = ctx.conn.execute("SELECT content, screen, actions FROM messages WHERE role='assistant' "
                           "AND chat=? ORDER BY id DESC LIMIT 1", (db.current_chat(ctx.conn),)
                           ).fetchone()
    if not row:
        raise ValueError("there is no answer to save yet")
    body = "\n\n".join(x for x in (row["content"], row["screen"]) if x)
    acts = json.loads(row["actions"]) if (row["actions"] or "").startswith("[") else []
    kind = "research" if any(x["name"] in RESEARCH_TOOLS for x in acts) else "document"
    folder = ctx.cfg.db_path.parent / "artifacts"
    folder.mkdir(parents=True, exist_ok=True)
    p, n = folder / f"{_slug(title)}.md", 1
    while p.exists():
        p, n = folder / f"{_slug(title)}-{n}.md", n + 1
    p.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    return f"Saved {kind}: {p}", {"kind": "to_trash", "path": str(p)}


_weather_cache: dict = {}   # {"at": monotonic time, "value": (temp, sky) or None}
WEATHER_TTL_S = 15 * 60


def _weather(cfg):
    """The weather is a network call (15 s timeout): ask at most once per TTL, failures too."""
    c = _weather_cache
    if not c or time.monotonic() - c["at"] > WEATHER_TTL_S:
        try:
            value = briefing.weather_short(cfg)
        except Exception:
            value = None
        c.update(at=time.monotonic(), value=value)
    return c["value"]


def glance(cfg, conn) -> list[dict]:
    """The `glance` socket message items; a source with nothing to say is left out."""
    out, now = [], day.now()
    if w := _weather(cfg):
        out.append({"icon": "sun", "value": w[0], "detail": w[1], "at": ""})
    left = [r for r in conn.execute("SELECT * FROM timetable WHERE day=? ORDER BY start",
                                    (now.weekday(),)) if r["end"] > f"{now:%H:%M}"]
    if left:
        c = left[0]
        out.append({"icon": "cal", "value": c["start"], "detail": c["subject"], "at": ""})
    tasks = conn.execute("SELECT title FROM tasks WHERE done=0 ORDER BY due='', due").fetchall()
    if tasks:
        out.append({"icon": "check", "value": str(len(tasks)), "detail": tasks[0][0], "at": ""})
    soon = conn.execute("SELECT at, text FROM reminders WHERE done=0 AND at >= ? AND at <= ? "
                        "ORDER BY at", (now.isoformat(timespec="minutes"),
                                        (now + timedelta(minutes=60)).isoformat(timespec="minutes")))
    out += [{"icon": "bell", "value": at[11:16], "detail": text, "at": at} for at, text in soon]
    return out
