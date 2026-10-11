"""Filename index over the allowed folders, and the find_files tool. Read-only."""
import os
import threading
import time

from jarvis import db
from jarvis.tools import tool

REFRESH_S = 1800


def build_index(db_path, roots, exclude) -> int:
    """Walk the roots (skipping excluded directory names) and replace the files table."""
    skip = set(exclude)
    rows = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip]
            for f in filenames:
                p = os.path.join(dirpath, f)
                try:
                    rows.append((p, f.lower(), os.path.getmtime(p)))
                except OSError:
                    pass
    c = db.connect(db_path)  # own connection: this runs on the indexer thread
    try:
        c.execute("BEGIN")
        c.execute("DELETE FROM files")
        c.executemany("INSERT OR REPLACE INTO files(path,name,mtime) VALUES(?,?,?)", rows)
        c.execute("COMMIT")
    finally:
        c.close()
    return len(rows)


def start_indexer(cfg, on_banner) -> None:
    def loop():
        from jarvis.tools import apps
        try:
            apps.APPS.update(apps.load_apps())
        except Exception as e:
            on_banner(f"App list unavailable ({e.__class__.__name__}); opening apps by name only")
        from jarvis import notes
        warned = set()
        while True:
            for name, job in (
                    ("File index", lambda: build_index(cfg.db_path, cfg.file_roots,
                                                       cfg.file_exclude)),
                    ("File content index", lambda: notes.index_contents(
                        cfg.db_path, cfg.file_roots, cfg.file_exclude + cfg.content_exclude,
                        skip=[cfg.app_dir]))):
                try:
                    job()
                except Exception as e:
                    if name not in warned:
                        warned.add(name)
                        on_banner(f"{name} failed ({e.__class__.__name__})")
            time.sleep(REFRESH_S)
    threading.Thread(target=loop, daemon=True).start()


def _like(word: str) -> str:
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@tool("Find the user's files by name (every word must appear in the file name). "
      "Returns full paths, newest first. Cannot read file contents.",
      describe=lambda a: f"search your files for '{a.get('query', '')}'", readonly=True,
      query="string: words from the file name", limit="?integer: how many, at most 20")
def find_files(ctx, query, limit=10):
    words = query.lower().split()
    if not ctx.conn.execute("SELECT 1 FROM files LIMIT 1").fetchone():
        return "The file index is still being built; try again in a minute.", None
    if not words:
        raise ValueError("give some words from the file name")
    sql = ("SELECT path FROM files WHERE " + " AND ".join(["name LIKE ? ESCAPE '\\'"] * len(words))
           + " ORDER BY mtime DESC LIMIT ?")
    rows = ctx.conn.execute(sql, [*map(_like, words), max(1, min(int(limit), 20))]).fetchall()
    if not rows:
        return f"No files match '{query}'.", None
    return "\n".join(f"- {r[0]}" for r in rows), None
