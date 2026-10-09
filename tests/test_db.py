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
