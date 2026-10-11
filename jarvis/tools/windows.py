"""Window tools on the Win32 API through ctypes (no pywin32)."""
import ctypes
import difflib
import time
from ctypes import wintypes as W
from pathlib import Path

from jarvis.tools import tool

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")
user32.GetForegroundWindow.restype = W.HWND
user32.MonitorFromWindow.restype = W.HANDLE
kernel32.OpenProcess.restype = W.HANDLE

SW_MINIMIZE, SW_MAXIMIZE, SW_RESTORE = 6, 3, 9
WM_CLOSE = 0x0010
VK_MENU, KEYUP = 0x12, 2
_ENUM = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
_SKIP_TITLES = {"Program Manager", "Windows Input Experience", "Settings"}


def _exe(hwnd) -> str:
    pid = W.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    buf, n = ctypes.create_unicode_buffer(520), W.DWORD(520)
    ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
    kernel32.CloseHandle(h)
    return Path(buf.value).stem.lower() if ok else ""


def _cloaked(hwnd) -> bool:
    """Suspended Store apps keep 'visible' windows that are not on screen."""
    v = W.DWORD()
    dwmapi.DwmGetWindowAttribute(W.HWND(hwnd), 14, ctypes.byref(v), 4)  # DWMWA_CLOAKED
    return bool(v.value)


def list_top() -> list[dict]:
    """Visible, unowned top-level windows with a title, in z-order (top first)."""
    out = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4) and not _cloaked(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                cls = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(hwnd, cls, 64)
                # "Ghost" is the stand-in Windows draws for a hung app's window
                if buf.value not in _SKIP_TITLES and cls.value != "Ghost":
                    out.append({"hwnd": hwnd, "title": buf.value, "exe": _exe(hwnd)})
        return True
    user32.EnumWindows(_ENUM(cb), 0)
    return out


def find_window(query: str, wins: list[dict]) -> int | list[dict]:
    """One hwnd, or the candidates (empty when nothing matches, several when ambiguous)."""
    q = query.lower().strip()
    hits = [x for x in wins if q == x["exe"] or q in x["title"].lower() or q in x["exe"]]
    if not hits:
        names = [x["title"].lower() for x in wins] + [x["exe"] for x in wins]
        close = set(difflib.get_close_matches(q, names, n=5, cutoff=0.6))
        hits = [x for x in wins if x["title"].lower() in close or x["exe"] in close]
    return hits[0]["hwnd"] if len(hits) == 1 else hits


def _target(window: str) -> int:
    r = find_window(window, list_top())
    if isinstance(r, int):
        return r
    if not r:
        raise ValueError(f"no open window matches '{window}'")
    raise ValueError("several windows match: " + "; ".join(
        f"{x['title']} ({x['exe']})" for x in r[:8]) + ". Ask the user which one.")


def _title(hwnd: int) -> str:
    return next((x["title"] for x in list_top() if x["hwnd"] == hwnd), str(hwnd))


# -------- thin Win32 wrappers (monkeypatched in tests)
def get_placement(hwnd: int) -> list[int]:
    wp = (ctypes.c_long * 11)()
    wp[0] = 44  # sizeof(WINDOWPLACEMENT)
    user32.GetWindowPlacement(W.HWND(hwnd), ctypes.byref(wp))
    return list(wp)


def set_placement(hwnd: int, data: list[int]) -> None:
    wp = (ctypes.c_long * 11)(*data)
    wp[0], wp[1] = 44, 0
    user32.SetWindowPlacement(W.HWND(hwnd), ctypes.byref(wp))


def _show(hwnd: int, cmd: int) -> None:
    user32.ShowWindow(W.HWND(hwnd), cmd)


def _work_area(hwnd: int) -> tuple[int, int, int, int]:
    mi = (ctypes.c_long * 10)()
    mi[0] = 40  # sizeof(MONITORINFO); rcWork is longs 5..8
    user32.GetMonitorInfoW(user32.MonitorFromWindow(W.HWND(hwnd), 2), ctypes.byref(mi))
    return mi[5], mi[6], mi[7] - mi[5], mi[8] - mi[6]


def _move(hwnd: int, x: int, y: int, cx: int, cy: int) -> None:
    user32.MoveWindow(W.HWND(hwnd), x, y, cx, cy, True)


def _is_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(W.HWND(hwnd)))


def _bring(hwnd: int) -> None:
    if user32.IsIconic(W.HWND(hwnd)):
        _show(hwnd, SW_RESTORE)
    # Windows only lets the foreground app take focus; a synthetic Alt press unlocks it.
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.SetForegroundWindow(W.HWND(hwnd))
    user32.keybd_event(VK_MENU, 0, KEYUP, 0)


def _post_close(hwnd: int) -> None:
    user32.PostMessageW(W.HWND(hwnd), WM_CLOSE, 0, 0)


def close_hwnds(hwnds: list[int]) -> None:
    for h in hwnds:
        if _is_window(h):
            _post_close(h)


def new_windows_after(fn, wait: float = 3.0) -> list[int]:
    """Run fn, then return the windows that appeared within `wait` seconds (for undo)."""
    before = {x["hwnd"] for x in list_top()}
    fn()
    end = time.monotonic() + wait
    while time.monotonic() < end:
        time.sleep(0.25)
        new = [x["hwnd"] for x in list_top() if x["hwnd"] not in before]
        if new:
            return new
    return []


def undo(u: dict) -> None:
    # Windows reuses handles, so only touch a window still owned by the same program.
    if u["kind"] == "close_windows":
        for h, exe in zip(u["hwnds"], u.get("exes") or [None] * len(u["hwnds"])):
            if _is_window(h) and (exe is None or _exe(h) == exe):
                _post_close(h)
        return
    if not _is_window(u["hwnd"]) or (u.get("exe") and _exe(u["hwnd"]) != u["exe"]):
        raise ValueError("that window no longer exists")
    if u["kind"] == "placement":
        set_placement(u["hwnd"], u["data"])
    elif u["kind"] == "focus":
        _bring(u["hwnd"])


# -------- tools
@tool("List the open windows (title and app).")
def list_windows(ctx):
    wins = list_top()[:30]
    return "\n".join(f"- {x['title']} ({x['exe']})" for x in wins) or "No windows are open.", None


@tool("Bring a window to the front.", window="string: app name or part of the window title")
def focus_window(ctx, window):
    h = _target(window)
    prev = user32.GetForegroundWindow()
    _bring(h)
    return f"Focused {_title(h)}.", ({"kind": "focus", "hwnd": prev, "exe": _exe(prev)}
                                     if prev else None)


def _placed(window: str, cmd: int, verb: str):
    h = _target(window)
    undo_info = {"kind": "placement", "hwnd": h, "data": get_placement(h), "exe": _exe(h)}
    _show(h, cmd)
    return f"{verb} {_title(h)}.", undo_info


@tool("Minimise a window.", window="string: app name or part of the window title")
def minimise_window(ctx, window):
    return _placed(window, SW_MINIMIZE, "Minimised")


@tool("Maximise a window.", window="string: app name or part of the window title")
def maximise_window(ctx, window):
    return _placed(window, SW_MAXIMIZE, "Maximised")


@tool("Snap a window to the left or right half of its screen.",
      window="string: app name or part of the window title", side="string: left or right")
def snap_window(ctx, window, side):
    if side not in ("left", "right"):
        raise ValueError("side must be left or right")
    h = _target(window)
    undo_info = {"kind": "placement", "hwnd": h, "data": get_placement(h), "exe": _exe(h)}
    _show(h, SW_RESTORE)
    x, y, cx, cy = _work_area(h)
    _move(h, x if side == "left" else x + cx // 2, y, cx // 2, cy)
    return f"Snapped {_title(h)} to the {side}.", undo_info


def _describe_close(args: dict) -> str:
    """Resolve the window before asking (fails on no/several matches), and pin it so the
    window closed is the one the user said yes to."""
    h = _target(str(args.get("window", "")))
    args["hwnd"] = h
    return f"close {_title(h)}"


@tool("Close a window (the app may still ask to save). Asks the user first.",
      confirm=True, describe=_describe_close,
      window="string: app name or part of the window title")
def close_window(ctx, window, hwnd=None):
    if hwnd is None or not _is_window(hwnd):
        raise ValueError("that window is no longer open")
    title = _title(hwnd)
    close_hwnds([hwnd])
    return f"Asked {title} to close.", None
