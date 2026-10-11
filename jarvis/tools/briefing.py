"""The daily briefing: everything about today in one place, each part failing softly."""
import ssl
from datetime import timedelta

from jarvis.tools import day, github, tool

WEATHER = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "foggy",
           48: "foggy", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
           61: "light rain", 63: "rain", 65: "heavy rain", 80: "rain showers",
           81: "rain showers", 82: "violent showers", 95: "thunderstorms",
           96: "thunderstorms with hail", 99: "thunderstorms with hail"}

COMPOSE = ("Compose the briefing: one or two spoken sentences (the most important thing first), "
           "then on screen the full briefing and a proposed time-blocked plan for today that "
           "fits around the classes and deadlines. Then call set_plan with that plan.")


def _weather_json(cfg) -> dict:
    import httpx
    import truststore
    r = httpx.get("https://api.open-meteo.com/v1/forecast", timeout=15,
                  verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
                  params={"latitude": cfg.latitude, "longitude": cfg.longitude,
                          "timezone": "auto", "forecast_days": 1,
                          "daily": "temperature_2m_max,temperature_2m_min,"
                                   "precipitation_probability_max,weather_code"})
    r.raise_for_status()
    return r.json()["daily"]


def weather(cfg) -> str:
    d = _weather_json(cfg)
    sky = WEATHER.get(d["weather_code"][0], "mixed weather")
    return (f"{sky}, {round(d['temperature_2m_max'][0])}°C high, "
            f"{round(d['temperature_2m_min'][0])}°C low, "
            f"{d['precipitation_probability_max'][0]}% chance of rain")


def _google(kind: str) -> str:
    """Filled in when Google is connected (jarvis.tools.google_tools)."""
    from jarvis.tools import google_tools
    return google_tools.briefing_part(kind)


def _part(label: str, fn) -> str:
    try:
        text = fn()
    except Exception as e:
        return f"{label}: unavailable ({e.__class__.__name__})"
    return f"{label}:\n{text}" if "\n" in text else f"{label}: {text}"


@tool("Gather today's briefing: date, weather, classes, deadlines, reminders, plan, calendar, "
      "important email and GitHub. Use it for 'brief me' or the morning briefing. " + COMPOSE,
      readonly=True, untrusted=True, max_chars=8000)
def daily_briefing(ctx):
    now, conn = day.now(), ctx.conn
    start, end = now.date().isoformat(), (now.date() + timedelta(days=1)).isoformat()
    reminders = conn.execute("SELECT at, text FROM reminders WHERE done=0 AND at >= ? AND at < ?"
                             " ORDER BY at", (start, end)).fetchall()
    parts = [
        f"Date: {now:%A %d %B %Y}, {now:%H:%M}",
        _part("Weather", lambda: weather(ctx.cfg)),
        _part("Classes today", lambda: day.timetable_text(conn, now.weekday())
              or "No classes today"),
        _part("Deadlines (overdue or within 7 days)", lambda: day.tasks_text(conn, 7) or "none"),
        _part("Reminders today", lambda: "\n".join(f"- {a[11:16]} {t}" for a, t in reminders)
              or "none"),
        _part("Calendar", lambda: _google("calendar")),
        _part("Email", lambda: _google("email")),
        _part("GitHub", github.summary),
    ]
    if plan := day.today_plan(conn):
        parts.append(f"Plan already saved for today:\n{plan}")
    return "\n\n".join(parts), None
