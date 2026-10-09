import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Literal

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, ts TEXT, role TEXT,
  content TEXT, screen TEXT, route TEXT, interrupted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY, ts TEXT, provider TEXT,
  model TEXT, tokens_in INTEGER, tokens_out INTEGER, cost_inr REAL);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # ponytail: one shared connection, autocommit; fine for one user, add a lock if writes ever race
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def add_message(conn, role: str, content: str, screen: str = "", route: str = "",
                interrupted: bool = False) -> int:
    cur = conn.execute(
        "INSERT INTO messages(ts,role,content,screen,route,interrupted) VALUES(?,?,?,?,?,?)",
        (datetime.now().isoformat(), role, content, screen, route, int(interrupted)))
    return cur.lastrowid


def recent_messages(conn, n: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT role,content,screen,route,interrupted FROM messages ORDER BY id DESC LIMIT ?",
        (n,)).fetchall()
    return [dict(r) for r in reversed(rows)]


def record_usage(conn, provider: str, model: str, tokens_in: int, tokens_out: int,
                 cost_inr: float, now: datetime | None = None) -> None:
    conn.execute(
        "INSERT INTO usage(ts,provider,model,tokens_in,tokens_out,cost_inr) VALUES(?,?,?,?,?,?)",
        ((now or datetime.now()).isoformat(), provider, model, tokens_in, tokens_out, cost_inr))


def month_spend_inr(conn, now: datetime | None = None) -> float:
    month = (now or datetime.now()).strftime("%Y-%m")
    row = conn.execute("SELECT COALESCE(SUM(cost_inr),0) FROM usage WHERE ts LIKE ?",
                       (month + "%",)).fetchone()
    return float(row[0])


def requests_today(conn, now: datetime | None = None) -> dict[str, int]:
    day = (now or datetime.now()).strftime("%Y-%m-%d")
    rows = conn.execute("SELECT provider, COUNT(*) FROM usage WHERE ts LIKE ? GROUP BY provider",
                        (day + "%",)).fetchall()
    return {p: n for p, n in rows}


def budget_state(spent: float, cap: float) -> Literal["ok", "warn", "over"]:
    if spent >= cap:
        return "over"
    return "warn" if spent >= 0.8 * cap else "ok"
