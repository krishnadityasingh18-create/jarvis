"""Changing the user's files, always reversibly: replaced and deleted things go to the
JARVIS trash (data/trash), which only the user empties. Nothing here is permanent."""
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from jarvis.tools import tool
from jarvis.tools.apps import _runs_code, hidden, inside_roots

MAX_WRITE = 2**20
# Notes and documents only: anything involving code goes to Claude Code (ask_claude).
WRITABLE = {".md", ".txt", ".rst", ".tex", ".csv", ".log", ".bib"}

def _trash(data_dir: Path) -> Path:
    """A fresh folder in the trash (data/trash) for one action's leftovers."""
    d = Path(data_dir).resolve() / "trash" / (
        datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
    d.mkdir(parents=True)
    return d


def can_touch(path: str, cfg) -> Path:
    """Resolve a path the user's files may be changed at, or raise with the reason."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ValueError("give the full path")
    # Windows drops trailing dots and spaces ("evil.bat." becomes evil.bat) and reads ":" as
    # an alternate data stream, so names like that would slip past the type checks
    if any(part != part.rstrip(". ") or ":" in part for part in p.parts[1:]):
        raise ValueError("that name ends in a dot or space or contains ':'")
    p = p.resolve()
    if not inside_roots(p, cfg.file_roots, cfg.file_exclude):
        raise ValueError("that path is outside the folders I may change")
    if hidden(p, cfg.file_roots):
        raise ValueError("I don't change hidden files or folders (settings that can run code)")
    for protected in (Path(cfg.app_dir).resolve(), (cfg.db_path.parent / "trash").resolve()):
        if p.is_relative_to(protected):
            raise ValueError("I don't change my own files or my trash")
    return p


def _text_target(path: str, cfg) -> Path:
    p = can_touch(path, cfg)
    if _runs_code(p.name) or p.suffix.lower() not in WRITABLE:
        raise ValueError(f"I only write notes and documents, not {p.suffix or 'that type'}; "
                         "code, scripts and configs go to Claude Code through ask_claude")
    return p


def _check_size(content: str) -> None:
    if len(content.encode("utf-8")) > MAX_WRITE:
        raise ValueError("that is more than 1 MB of text")


def _rename(src, dst) -> None:
    """All-or-nothing: a plain rename never half-moves a folder (shutil.move falls back to
    copy-then-delete, which leaves a gutted folder when one file inside is open)."""
    try:
        os.rename(src, dst)
    except OSError as e:
        raise ValueError(f"{Path(src).name} (or something in it) is open or locked; nothing "
                         f"was changed ({e.__class__.__name__})") from None


def _backup(p: Path, cfg) -> str:
    d = _trash(cfg.db_path.parent)
    shutil.copy2(p, d / p.name)
    return str(d / p.name)


@tool("Create a new text file (notes, code, markdown). Fails if it already exists.",
      path="string: full path of the new file", content="string: the file's text")
def create_file(ctx, path, content):
    p = _text_target(path, ctx.cfg)
    _check_size(content)
    if p.exists():
        raise ValueError("that file already exists (use write_file or edit_file)")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "x", encoding="utf-8", newline="") as f:
        f.write(content)
    return f"Created {p}.", {"kind": "to_trash", "path": str(p)}


@tool("Replace the whole text of an existing file. The old version is kept in the JARVIS "
      "trash. Asks first.", confirm=True,
      describe=lambda a: f"overwrite {Path(str(a.get('path', ''))).name}",
      path="string: full path", content="string: the new text")
def write_file(ctx, path, content):
    p = _text_target(path, ctx.cfg)
    _check_size(content)
    if not p.is_file():
        raise ValueError("that file does not exist (use create_file)")
    backup = _backup(p, ctx.cfg)
    p.write_text(content, encoding="utf-8", newline="")
    return f"Overwrote {p}.", {"kind": "restore_version", "path": str(p), "backup": backup}


@tool("Replace one exact snippet of text in a file with new text. The snippet must appear "
      "exactly once. The old version is kept in the JARVIS trash. Asks first.", confirm=True,
      describe=lambda a: f"edit {Path(str(a.get('path', ''))).name}",
      path="string: full path", old="string: the exact text to replace",
      new="string: the replacement text")
def edit_file(ctx, path, old, new):
    p = _text_target(path, ctx.cfg)
    if not p.is_file():
        raise ValueError("that file does not exist")
    with open(p, encoding="utf-8", newline="") as f:
        text = f.read()
    if old not in text and "\r\n" in text:  # the model writes \n; the file may use \r\n
        old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
    n = text.count(old) if old else 0
    if n != 1:
        raise ValueError("that text was not found" if n == 0 else
                         f"that text appears {n} times; give a longer, unique snippet")
    _check_size(text.replace(old, new))
    backup = _backup(p, ctx.cfg)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace(old, new))
    return f"Edited {p}.", {"kind": "restore_version", "path": str(p), "backup": backup}


@tool("Create a folder (and any missing parent folders).", path="string: full path")
def create_folder(ctx, path):
    p = can_touch(path, ctx.cfg)
    if p.exists():
        raise ValueError("that already exists")
    top = p
    while not top.parent.exists():   # undo removes everything this created, top-down
        top = top.parent
    p.mkdir(parents=True)
    return f"Created folder {p}.", {"kind": "remove_dirs", "path": str(p), "top": str(top)}


@tool("Move or rename a file or folder. Fails if the destination exists. Asks first.",
      confirm=True,
      describe=lambda a: (f"move {Path(str(a.get('src', ''))).name} to "
                          f"{Path(str(a.get('dst', ''))).name}"),
      src="string: full path now", dst="string: full new path")
def move_path(ctx, src, dst):
    s, d = can_touch(src, ctx.cfg), can_touch(dst, ctx.cfg)
    if not s.exists():
        raise ValueError("there is nothing at that path")
    if d.exists():
        raise ValueError("something already exists at the destination")
    if _runs_code(d.name) and d.suffix.lower() != s.suffix.lower():
        raise ValueError("I won't rename a file into a program or script type")
    d.parent.mkdir(parents=True, exist_ok=True)
    _rename(s, d)
    return f"Moved {s} to {d}.", {"kind": "move_back", "src": str(s), "dst": str(d)}


@tool("Delete a file or folder by moving it to the JARVIS trash (it can be restored). "
      "Asks first.", confirm=True,
      describe=lambda a: f"delete {Path(str(a.get('path', ''))).name} (it goes to the JARVIS trash)",
      path="string: full path")
def delete_path(ctx, path):
    p = can_touch(path, ctx.cfg)
    if not p.exists():
        raise ValueError("there is nothing at that path")
    where = _trash(ctx.cfg.db_path.parent) / p.name
    _rename(p, where)
    return f"Moved {p} to the JARVIS trash.", {"kind": "restore", "path": str(p),
                                               "from": str(where)}


def _free(p: Path) -> None:
    if p.exists():
        raise ValueError(f"something now exists at {p}; I won't overwrite it")


def undo(u: dict, data_dir: Path) -> None:
    kind, p = u["kind"], Path(u.get("path") or u.get("src"))
    if kind == "to_trash":       # undo create_file
        if p.exists():
            _rename(p, _trash(data_dir) / p.name)
    elif kind == "restore_version":
        if not Path(u["backup"]).exists():
            raise ValueError("the old version is no longer in the trash")
        if p.exists():
            _rename(p, _trash(data_dir) / p.name)   # keep the newer version too
        shutil.copy2(u["backup"], p)
    elif kind == "remove_dirs":
        top = Path(u["top"])
        if any(f.is_file() for f in top.rglob("*")):
            raise ValueError("that folder isn't empty any more")
        shutil.rmtree(top)       # only empty folders this action made
    elif kind == "move_back":
        _free(p)
        if not Path(u["dst"]).exists():
            raise ValueError("it's no longer where I moved it")
        _rename(u["dst"], p)
    elif kind == "restore":
        _free(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        _rename(u["from"], p)


KINDS = {"to_trash", "restore_version", "remove_dirs", "move_back", "restore"}
