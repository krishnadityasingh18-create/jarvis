"""What the island shows about the desk: the song playing (Windows media session) and the
system volume. Both are optional: without the libraries, or off Windows, they report nothing."""
import asyncio
import base64
import threading

PLAYING = 4   # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
ACTIONS = {"toggle": "try_toggle_play_pause_async", "next": "try_skip_next_async",
           "prev": "try_skip_previous_async"}


async def _session():
    try:
        from winrt.windows.media.control import \
            GlobalSystemMediaTransportControlsSessionManager as Manager
    except ImportError:
        return None
    return (await Manager.request_async()).get_current_session()


def _off_thread(f):
    """Run a WinRT coroutine on a worker thread with its own loop. Once comtypes (pycaw) has made
    the main thread a single-threaded COM apartment, WinRT calls there can block it for good."""
    async def run(*a, **kw):
        return await asyncio.to_thread(asyncio.run, f(*a, **kw))
    return run


@_off_thread
async def media(thumb: bool = False) -> dict | None:
    """The current song (title, artist, playing, app), or None when nothing is playing.
    thumb=True adds the cover as a data: URL (only asked for when the song changes)."""
    s = await _session()
    if s is None:
        return None
    p = await s.try_get_media_properties_async()
    if not (p.title or p.artist):
        return None
    out = {"title": p.title, "artist": p.artist, "app": s.source_app_user_model_id,
           "playing": int(s.get_playback_info().playback_status) == PLAYING}
    if thumb:
        out["thumb"] = await _cover(p.thumbnail)
    return out


async def _cover(ref) -> str:
    if ref is None:
        return ""
    from winrt.windows.storage.streams import Buffer, InputStreamOptions
    stream = await ref.open_read_async()
    buf = Buffer(min(stream.size, 2_000_000))
    await stream.read_async(buf, buf.capacity, InputStreamOptions.READ_AHEAD)
    data = bytes(buf)
    kind = "png" if data[:4] == b"\x89PNG" else "jpeg"
    return f"data:image/{kind};base64," + base64.b64encode(data).decode()


@_off_thread
async def media_control(action: str) -> bool:
    """Play/pause, next or previous on whatever is playing. False if there's nothing."""
    s = await _session()
    if s is None or action not in ACTIONS:
        return False
    return bool(await getattr(s, ACTIONS[action])())


def watch_volume(on_change, every_s: float = 0.2, stop: threading.Event | None = None) -> None:
    """Call on_change(level 0-1, muted) whenever the system volume changes (not at start).
    Runs on its own thread (COM is per-thread); returns at once if pycaw isn't installed."""
    try:
        import comtypes
        from pycaw.pycaw import AudioUtilities
    except ImportError:
        return
    stop = stop or threading.Event()

    def loop():
        comtypes.CoInitialize()
        last, ep, n = None, None, 0
        while not stop.wait(every_s):
            n += 1
            if n % 25 == 0:
                ep = None   # every ~5 s: follow a new default device (headphones plugged in)
            try:
                ep = ep or AudioUtilities.GetSpeakers().EndpointVolume
                now = (round(ep.GetMasterVolumeLevelScalar(), 3), bool(ep.GetMute()))
            except Exception:   # the output device changed or went away: look it up again
                ep = None
                continue
            if last is not None and now != last:
                on_change(*now)
            last = now

    threading.Thread(target=loop, daemon=True, name="volume").start()
