import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Literal

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, ts TEXT, role TEXT,
  content TEXT, screen TEXT, route TEXT, interrupted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY, ts TEXT, provider TEXT,
  model TEXT, tokens_in INTEGER, tokens_out INTEGER, cost_inr REAL);
CREATE TABLE IF NOT EXISTS actions (id INTEGER PRIMARY KEY, ts TEXT, tool TEXT, args TEXT,
  result TEXT, status TEXT, undo TEXT, agent TEXT);
CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, ts TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, name TEXT, mtime REAL);
CREATE INDEX IF NOT EXISTS files_name ON files(name);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # ponytail: one shared connection, autocommit; fine for one user, add a lock if writes ever race
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # the file indexer writes from its own connection
    conn.executescript(SCHEMA)
    if "actions" not in [r[1] for r in conn.execute("PRAGMA table_info(messages)")]:
        conn.execute("ALTER TABLE messages ADD COLUMN actions TEXT DEFAULT ''")
    return conn


def add_message(conn, role: str, content: str, screen: str = "", route: str = "",
                interrupted: bool = False, actions: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO messages(ts,role,content,screen,route,interrupted,actions) "
        "VALUES(?,?,?,?,?,?,?)",
        (datetime.now().isoformat(), role, content, screen, route, int(interrupted), actions))
    return cur.lastrowid


def recent_messages(conn, n: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT role,content,screen,route,interrupted,actions FROM messages ORDER BY id DESC LIMIT ?",
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


def _action(row) -> dict:
    a = dict(row)
    a["args"] = json.loads(a["args"])
    a["undo"] = json.loads(a["undo"]) if a["undo"] else None
    return a


def log_action(conn, tool: str, args: dict, result: str, status: str, undo: dict | None,
               agent: str | None) -> int:
    cur = conn.execute(
        "INSERT INTO actions(ts,tool,args,result,status,undo,agent) VALUES(?,?,?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), tool, json.dumps(args), result, status,
         json.dumps(undo) if undo else None, agent))
    return cur.lastrowid


def update_action(conn, id: int, **fields) -> None:
    for k, v in fields.items():
        assert k in ("result", "status", "undo")
        conn.execute(f"UPDATE actions SET {k}=? WHERE id=?",
                     (json.dumps(v) if k == "undo" and v else v, id))


def get_action(conn, id: int) -> dict | None:
    row = conn.execute("SELECT * FROM actions WHERE id=?", (id,)).fetchone()
    return _action(row) if row else None


def recent_actions(conn, n: int = 30) -> list[dict]:
    return [_action(r) for r in conn.execute("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (n,))]


def add_memory(conn, text: str) -> int:
    return conn.execute("INSERT INTO memories(ts,text) VALUES(?,?)",
                        (datetime.now().isoformat(timespec="seconds"), text)).lastrowid


def delete_memory(conn, id: int) -> str | None:
    row = conn.execute("SELECT text FROM memories WHERE id=?", (id,)).fetchone()
    conn.execute("DELETE FROM memories WHERE id=?", (id,))
    return row[0] if row else None


def restore_memory(conn, id: int, text: str) -> None:
    conn.execute("INSERT OR REPLACE INTO memories(id,ts,text) VALUES(?,?,?)",
                 (id, datetime.now().isoformat(timespec="seconds"), text))


def memories(conn, n: int = 50) -> list[dict]:
    rows = conn.execute("SELECT id,text FROM memories ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    return [dict(r) for r in reversed(rows)]
