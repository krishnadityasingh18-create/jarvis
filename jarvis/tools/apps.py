"""Open apps and files, run approved scripts."""
import ctypes
import difflib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from jarvis.tools import tool
from jarvis.tools.windows import new_windows_after

APPS: dict[str, str] = {}   # Start-menu name -> AppID, filled by the indexer thread
SCRIPTS_DIR = Path(__file__).parents[1] / "scripts"
EXECUTABLE = {".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".msi", ".lnk", ".py", ".com",
              ".scr", ".url", ".msc", ".jar", ".wsf", ".hta", ".cpl", ".reg"}
RUNNERS = {".py": lambda f: [sys.executable, str(f)],
           ".ps1": lambda f: ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                              str(f)],
           ".bat": lambda f: ["cmd", "/c", str(f)]}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _runs_code(name: str) -> bool:
    """Our list plus Windows' own list of types that can run code (.chm, .pif, .scf...)."""
    ext = Path(name).suffix.lower()
    return bool(ext) and (ext in EXECUTABLE or bool(ctypes.windll.shlwapi.AssocIsDangerous(ext)))


def load_apps() -> dict[str, str]:
    """Start-menu and Store apps via Get-StartApps (~2 s)."""
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          "Get-StartApps | ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                         encoding="utf-8", timeout=60, creationflags=NO_WINDOW, check=True)
    data = json.loads(out.stdout)
    return {a["Name"]: a["AppID"] for a in (data if isinstance(data, list) else [data])}


def match_app(name: str, apps: dict[str, str]) -> str | None:
    q = name.lower().strip()
    names = {n.lower(): n for n in apps}
    if q in names:
        return apps[names[q]]
    subs = sorted((n for n in names if q in n), key=len)
    if subs:
        return apps[names[subs[0]]]
    close = difflib.get_close_matches(q, names, n=1, cutoff=0.6)
    return apps[names[close[0]]] if close else None


def _opened(target, label: str):
    from jarvis.tools.windows import _exe
    hwnds = new_windows_after(lambda: os.startfile(target))
    return f"Opened {label}.", ({"kind": "close_windows", "hwnds": hwnds,
                                 "exes": [_exe(h) for h in hwnds]} if hwnds else None)


@tool("Open an installed app by name.", name="string: the app's name, e.g. Chrome")
def open_app(ctx, name):
    if not APPS:  # app list unavailable: let Windows try the name, but only a plain name
        if re.search(r"[\\/:]", name) or _runs_code(name):
            raise ValueError("I can only open apps by name, not paths, links or programs")
        return _opened(name, name)
    app_id = match_app(name, APPS)
    if app_id is None:
        close = difflib.get_close_matches(name.lower(), [n.lower() for n in APPS], n=5, cutoff=0.3)
        raise ValueError(f"no app called '{name}'" + (f"; closest: {', '.join(close)}" if close
                                                       else ""))
    label = next(n for n, a in APPS.items() if a == app_id)
    return _opened("shell:AppsFolder\\" + app_id, label)


def inside_roots(path: Path, roots: list[Path], exclude: list[str] = ()) -> bool:
    p = path.resolve()
    for r in roots:
        r = r.resolve()
        if p.is_relative_to(r):
            return not set(p.relative_to(r).parts) & set(exclude)
    return False


@tool("Open a file or folder in its default app. Only inside the user's allowed folders; "
      "never programs or scripts.", describe=lambda a: f"open {Path(a.get('path', '')).name}",
      path="string: full path, e.g. from find_files")
def open_path(ctx, path):
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ValueError("give the full path (use find_files to get it)")
    p = p.resolve()
    if not inside_roots(p, ctx.cfg.file_roots, ctx.cfg.file_exclude):
        raise ValueError("that path is outside the folders I'm allowed to open")
    if _runs_code(p.name):
        raise ValueError("that is a program or script; only approved scripts can run, "
                         "through run_script")
    if not p.exists():
        raise ValueError("that path does not exist")
    return _opened(p, p.name)


def _scripts() -> list[Path]:
    if not SCRIPTS_DIR.is_dir():
        return []
    return sorted(f for f in SCRIPTS_DIR.iterdir() if f.suffix.lower() in RUNNERS)


@tool("Run one of the user's approved scripts from the jarvis/scripts folder. Asks first.",
      confirm=True, describe=lambda a: f"run the script {a.get('name', '')}",
      name="string: the script's file name or name without extension")
def run_script(ctx, name):
    n = name.lower().strip()
    f = next((s for s in _scripts() if n in (s.name.lower(), s.stem.lower())), None)
    if f is None:
        raise ValueError(f"no approved script called '{name}'; available: "
                         + (", ".join(s.name for s in _scripts()) or "none"))
    r = subprocess.run(RUNNERS[f.suffix.lower()](f), cwd=SCRIPTS_DIR, capture_output=True,
                       encoding="utf-8", errors="replace", timeout=60, creationflags=NO_WINDOW)
    out = (r.stdout + r.stderr).strip()[-2000:]
    return f"{f.name}: exit {r.returncode}\n```\n{out}\n```", None
