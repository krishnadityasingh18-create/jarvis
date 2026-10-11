from datetime import datetime
from pathlib import Path

from jarvis import db
from jarvis.config import load_config


def test_recent_messages_oldest_first_and_limited(tmp_path):
    c = db.connect(tmp_path / "j.db")
    for i in range(25):
        db.add_message(c, "user", f"m{i}")
    r = db.recent_messages(c, 20)
    assert len(r) == 20 and r[0]["content"] == "m5" and r[-1]["content"] == "m24"


def test_interrupted_flag_roundtrip(tmp_path):
    c = db.connect(tmp_path / "j.db")
    db.add_message(c, "assistant", "Hello sir.", interrupted=True)
    assert db.recent_messages(c)[0]["interrupted"] == 1


def test_month_spend_ignores_last_month(tmp_path):
    c = db.connect(tmp_path / "j.db")
    c.execute("INSERT INTO usage(ts,provider,model,tokens_in,tokens_out,cost_inr) "
              "VALUES('2026-09-30T23:00:00','g','m',1,1,50)")
    db.record_usage(c, "g", "m", 1, 1, 20.0, now=datetime(2026, 10, 9, 12))
    assert db.month_spend_inr(c, datetime(2026, 10, 9)) == 20.0


def test_requests_today_per_provider(tmp_path):
    c = db.connect(tmp_path / "j.db")
    db.record_usage(c, "groq", "m", 1, 1, 0)
    db.record_usage(c, "groq", "m", 1, 1, 0)
    assert db.requests_today(c) == {"groq": 2}


def test_budget_state_thresholds():
    assert db.budget_state(239, 300) == "ok"
    assert db.budget_state(240, 300) == "warn"
    assert db.budget_state(300, 300) == "over"


def test_load_config_reads_routes():
    cfg = load_config(Path("config.toml"))
    assert cfg.default_route == "fast" and cfg.routes["local"].key == ""
    assert cfg.voice == "bm_lewis" and cfg.monthly_inr == 300
    assert '"sir"' in cfg.persona


def test_action_log_roundtrip(conn):
    aid = db.log_action(conn, "open_app", {"name": "Chrome"}, "ok", "done",
                        {"kind": "close_windows", "hwnds": [1]}, None)
    db.update_action(conn, aid, status="undone")
    a = db.recent_actions(conn)[0]
    assert a["id"] == aid and a["status"] == "undone"
    assert a["args"] == {"name": "Chrome"} and a["undo"]["hwnds"] == [1]
    assert db.get_action(conn, aid)["tool"] == "open_app"
    assert db.get_action(conn, 999) is None


def test_memory_add_delete_restore(conn):
    mid = db.add_memory(conn, "lab on Thursdays")
    assert db.delete_memory(conn, mid) == "lab on Thursdays"
    assert db.memories(conn) == []
    db.restore_memory(conn, mid, "lab on Thursdays")
    assert [(m["id"], m["text"]) for m in db.memories(conn)] == [(mid, "lab on Thursdays")]


def test_messages_actions_column(conn):
    db.add_message(conn, "assistant", "hi", actions="open_app Chrome → done")
    assert db.recent_messages(conn)[-1]["actions"] == "open_app Chrome → done"


def test_old_db_gets_actions_column(tmp_path):
    import sqlite3
    p = tmp_path / "old.db"
    old = sqlite3.connect(p)
    old.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, ts TEXT, role TEXT, "
                "content TEXT, screen TEXT, route TEXT, interrupted INTEGER DEFAULT 0)")
    old.commit()
    old.close()
    c = db.connect(p)
    cols = [r[1] for r in c.execute("PRAGMA table_info(messages)")]
    assert "actions" in cols
