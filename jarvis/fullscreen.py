"""Is something fullscreen (a game, a video, a presentation)? Then the island stays out of the way."""
import ctypes

BUSY, D3D_FULL_SCREEN, PRESENTATION = 2, 3, 4   # QUERY_USER_NOTIFICATION_STATE values


def _query() -> int:
    state = ctypes.c_int()
    ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
    return state.value


def state(query=None) -> bool:
    try:
        return (query or _query)() in (BUSY, D3D_FULL_SCREEN, PRESENTATION)
    except Exception:
        return False
