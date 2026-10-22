import json
import sqlite3
import threading
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
CREATE TABLE IF NOT EXISTS timetable (id INTEGER PRIMARY KEY, day INTEGER, start TEXT,
  end TEXT, subject TEXT, room TEXT);
CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, title TEXT, due TEXT,
  done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY, at TEXT, text TEXT,
  done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS plans (day TEXT PRIMARY KEY, text TEXT);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS docs (path TEXT PRIMARY KEY, mtime REAL, size INTEGER,
  chunks INTEGER, error TEXT);
CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY, path TEXT, page INTEGER, text TEXT,
  vec BLOB);
CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text); END;
"""


class _Conn(sqlite3.Connection):
    """Shared by the event loop and tool threads (asyncio.to_thread): one statement at a
    time, so an INSERT's lastrowid can't be another thread's row."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.lock = threading.Lock()

    def execute(self, *a):
        with self.lock:
            return super().execute(*a)


def connect(path: Path | str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, factory=_Conn)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # the file indexer writes from its own connection
    conn.executescript(SCHEMA)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)")]
    if "actions" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN actions TEXT DEFAULT ''")
    if "private" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN private INTEGER DEFAULT 0")
    if "chat" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN chat INTEGER DEFAULT 0")
    if "tainted" not in [r[1] for r in conn.execute("PRAGMA table_info(plans)")]:
        conn.execute("ALTER TABLE plans ADD COLUMN tainted INTEGER DEFAULT 0")
    if "call" not in [r[1] for r in conn.execute("PRAGMA table_info(reminders)")]:
        conn.execute("ALTER TABLE reminders ADD COLUMN call INTEGER DEFAULT 0")
    if "seconds" not in [r[1] for r in conn.execute("PRAGMA table_info(usage)")]:
        # phone calls: their length once Vapi reports them ended (NULL until then)
        conn.execute("ALTER TABLE usage ADD COLUMN seconds REAL")
    return conn


def add_message(conn, role: str, content: str, screen: str = "", route: str = "",
                interrupted: bool = False, actions: str = "", private: bool = False) -> int:
    cur = conn.execute(
        "INSERT INTO messages(ts,role,content,screen,route,interrupted,actions,private,chat) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (datetime.now().isoformat(), role, content, screen, route, int(interrupted), actions,
         int(private), current_chat(conn)))
    return cur.lastrowid


def recent_messages(conn, n: int = 20) -> list[dict]:
    """The current chat's last n messages (the model's memory of this conversation)."""
    rows = conn.execute(
        "SELECT role,content,screen,route,interrupted,actions,private FROM messages "
        "WHERE chat=? ORDER BY id DESC LIMIT ?",
        (current_chat(conn), n)).fetchall()
    return [dict(r) for r in reversed(rows)]


def current_chat(conn) -> int:
    row = conn.execute("SELECT value FROM kv WHERE key='chat'").fetchone()
    return int(row[0]) if row else 0


def open_chat(conn, chat: int) -> None:
    conn.execute("INSERT OR REPLACE INTO kv(key, value) VALUES('chat', ?)", (str(chat),))


def new_chat(conn) -> int:
    """Start a fresh conversation; an empty current chat is reused, never filed."""
    cur = current_chat(conn)
    if not conn.execute("SELECT 1 FROM messages WHERE chat=?", (cur,)).fetchone():
        return cur
    nxt = conn.execute("SELECT MAX(chat) FROM messages").fetchone()[0] + 1
    open_chat(conn, nxt)
    return nxt


def chats(conn, n: int = 30) -> list[dict]:
    """Past conversations, newest first: id, title (first thing the user said), size, last ts."""
    rows = conn.execute(
        "SELECT chat, COUNT(*) AS count, MAX(ts) AS ts, (SELECT content FROM messages m2 "
        " WHERE m2.chat=m.chat AND role='user' ORDER BY id LIMIT 1) AS title "
        "FROM messages m GROUP BY chat ORDER BY MAX(id) DESC LIMIT ?", (n,)).fetchall()
    return [{"id": r["chat"], "title": r["title"] or "(no words)", "count": r["count"],
             "ts": r["ts"]} for r in rows]


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
