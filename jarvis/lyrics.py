"""Synced lyrics for the song playing, from LRCLIB (lrclib.net: free, no key)."""
import re

API = "https://lrclib.net/api"
UA = "JARVIS desktop assistant (https://github.com/krishnadityasingh18-create/jarvis)"
_TAG = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
_JUNK = re.compile(r"\s*[(\[][^)\]]*(official|video|audio|lyric|visuali[sz]er|remaster|hd|4k|mv)[^)\]]*[)\]]", re.I)
_cache: dict[tuple[str, str], list] = {}


def parse(lrc: str) -> list[tuple[float, str]]:
    """'[01:02.50] line' -> [(62.5, 'line')], sorted; a line with several tags appears at each."""
    out = []
    for row in lrc.splitlines():
        tags = _TAG.findall(row)
        text = _TAG.sub("", row).strip()
        out += [(int(m) * 60 + float(s), text) for m, s in tags]
    return sorted(out)


def clean(title: str, artist: str) -> tuple[str, str]:
    """YouTube-style names: drop '(Official Video)' and the like, ' - Topic', 'VEVO', and
    an 'Artist - ' prefix repeated in the title."""
    artist = re.sub(r"\s*-\s*Topic$|VEVO$", "", artist).strip()
    title = _JUNK.sub("", title).strip()
    if artist and title.lower().startswith(artist.lower() + " - "):
        title = title[len(artist) + 3:]
    return title, artist


def fetch(title: str, artist: str, duration: float = 0) -> list[tuple[float, str]]:
    """Timed lines for the song, [] if LRCLIB has none synced (or is unreachable). Cached."""
    title, artist = clean(title, artist)
    key = (title.lower(), artist.lower())
    if key not in _cache:
        lines = _lookup(title, artist, duration)
        if lines is None:
            return []   # network trouble: don't cache, try again next time it plays
        _cache[key] = lines
    return _cache[key]


def _lookup(title: str, artist: str, duration: float) -> list | None:
    import httpx
    try:
        with httpx.Client(base_url=API, headers={"User-Agent": UA}, timeout=10) as c:
            q = {"track_name": title, "artist_name": artist}
            r = c.get("/get", params=q | ({"duration": round(duration)} if duration else {}))
            hits = [r.json()] if r.status_code == 200 else []
            if not any(h.get("syncedLyrics") for h in hits):
                r = c.get("/search", params=q if artist else {"q": title})
                hits = r.json() if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return None
    for h in hits:
        if h.get("syncedLyrics"):
            return parse(h["syncedLyrics"])
    return []

