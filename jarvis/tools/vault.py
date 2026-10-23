"""The vault: things the user asked JARVIS to keep (notes, links, copies of files). Undoable."""
import asyncio
import hashlib
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from jarvis.tools import edit, files, tool

MAX_QUIET_BYTES = 100 * 1024 * 1024


def folder(cfg) -> Path:
    return cfg.db_path.parent / "vault"


def items(conn) -> list[dict]:
    """The `vault` socket message items, newest first."""
    return [{k: r[k] for k in ("id", "kind", "title", "text", "path", "url", "ts")}
            for r in conn.execute("SELECT * FROM vault ORDER BY id DESC")]


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


@tool(local_only=True, description="Keep something in the user's vault: a note (text), a copy of "
      "a file (path) or a link (url). Use it for 'keep this' / 'save this for later'. Give "
      "exactly one of text, path or url.",
      text="?string: a note to keep", path="?string: full path of a file to keep a copy of",
      url="?string: a link to keep", title="?string: a short title")
async def keep(ctx, text="", path="", url="", title=""):
    if sum(bool(x) for x in (text, path, url)) != 1:
        raise ValueError("give exactly one of text, path or url")
    src = None
    if path:
        src = edit.can_touch(path, ctx.cfg)
        if not src.is_file():
            raise ValueError("that isn't a file")
        size = src.stat().st_size
        if size > MAX_QUIET_BYTES and not await ctx.confirm(
                f"Keep {src.name} ({size / 2**20:.0f} MB) in the vault, sir?"):
            return "The user declined.", None
        kind, sha, title = "file", await asyncio.to_thread(_sha, src), title or src.name
    elif url:
        u = urlparse(url)
        kind, sha, title = "link", hashlib.sha256(url.encode()).hexdigest(), \
            title or (u.netloc + u.path)[:60]
    else:
        kind, sha, title = "note", hashlib.sha256(text.encode()).hexdigest(), title or text[:60]
    if ctx.conn.execute("SELECT 1 FROM vault WHERE sha=?", (sha,)).fetchone():
        return "That's already in your vault.", None
    rid = ctx.conn.execute("INSERT INTO vault(ts, kind, title, text, path, url, sha) "
                           "VALUES(?,?,?,?,?,?,?)",
                           (datetime.now().isoformat(timespec="seconds"), kind, title, text,
                            "", url, sha)).lastrowid
    if src:
        dest = folder(ctx.cfg) / f"{rid}-{src.name}"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copy2, src, dest)
        except OSError:
            ctx.conn.execute("DELETE FROM vault WHERE id=?", (rid,))
            raise
        ctx.conn.execute("UPDATE vault SET path=? WHERE id=?", (str(dest), rid))
    return f"Kept {title} in the vault.", {"kind": "unkeep", "id": rid}


@tool("Search the user's vault (kept notes, links and files) by words in the title, text or link.",
      readonly=True, query="string: what to look for")
def find_in_vault(ctx, query):
    like = files._like(query.strip())
    rows = ctx.conn.execute(
        "SELECT id, title, kind FROM vault WHERE title LIKE ? ESCAPE '\\' OR "
        "text LIKE ? ESCAPE '\\' OR url LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT 10",
        (like, like, like)).fetchall()
    if not rows:
        return f"Nothing in the vault matches '{query}'.", None
    return "\n".join(f"[{r['id']}] {r['title']} — {r['kind']}" for r in rows), None


@tool(local_only=True, description="Remove an item from the vault by its [id]. A kept file goes to "
      "the JARVIS trash; it can be undone.", id="integer: the vault item id")
def remove_from_vault(ctx, id):
    row = ctx.conn.execute("SELECT * FROM vault WHERE id=?", (int(id),)).fetchone()
    if not row:
        raise ValueError(f"there is nothing in the vault with id {id}")
    row, where = dict(row), ""
    if row["path"] and Path(row["path"]).exists():
        where = str(edit._trash(ctx.cfg.db_path.parent) / Path(row["path"]).name)
        edit._rename(row["path"], where)
    ctx.conn.execute("DELETE FROM vault WHERE id=?", (row["id"],))
    return f"Removed {row['title']} from the vault.", {"kind": "rekeep", "row": row, "from": where}


def undo(conn, u: dict, data_dir: Path) -> None:
    if u["kind"] == "unkeep":
        row = conn.execute("SELECT path FROM vault WHERE id=?", (u["id"],)).fetchone()
        conn.execute("DELETE FROM vault WHERE id=?", (u["id"],))
        if row and row[0] and Path(row[0]).exists():
            edit._rename(row[0], edit._trash(data_dir) / Path(row[0]).name)
    elif u["kind"] == "rekeep":
        row = u["row"]
        if u["from"] and Path(u["from"]).exists():
            edit._free(Path(row["path"]))
            edit._rename(u["from"], row["path"])
        conn.execute(f"INSERT OR REPLACE INTO vault({', '.join(row)}) VALUES "
                     f"({', '.join('?' * len(row))})", tuple(row.values()))


KINDS = {"unkeep", "rekeep"}
