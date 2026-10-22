from datetime import datetime, timedelta
from pathlib import Path

from jarvis import artifacts, db
from jarvis.tools import briefing, run
from jarvis.tools.briefing import weather_short as real_weather_short   # conftest patches the module's
from tests.test_tools import call, make_ctx


def test_items_lists_created_files_newest_first_and_marks_missing(cfg, conn, tmp_path):
    a = tmp_path / "plan.md"
    a.write_text("Week plan\n- gym")
    db.log_action(conn, "create_file", {"path": str(a), "content": "x"}, "Created", "done", None, None)
    db.log_action(conn, "create_file", {"path": str(tmp_path / "gone.md"), "content": "x"},
                  "Created", "done", None, None)
    gone, plan = artifacts.items(conn)
    assert gone["exists"] is False and plan["exists"] is True and plan["preview"].startswith("Week plan")


def test_items_skip_failed_and_declined(cfg, conn, tmp_path):
    db.log_action(conn, "create_file", {"path": str(tmp_path / "a.md")}, "x", "declined", None, None)
    assert artifacts.items(conn) == []


def test_items_dedupe_by_path_and_list_commits(cfg, conn, tmp_path):
    for _ in range(2):
        db.log_action(conn, "edit_file", {"path": str(tmp_path / "a.md")}, "Edited", "done", None, None)
    db.log_action(conn, "ask_claude", {"task": "x", "project": "demo"},
                  "Done.\n\nCommits in demo:\nabc123 add hello", "done",
                  {"kind": "git_revert", "repo": str(tmp_path / "demo")}, None)
    commit, file = artifacts.items(conn)
    assert file["kind"] == "file" and commit["kind"] == "commit"
    assert commit["preview"] == "add hello" and commit["path"] == str(tmp_path / "demo")


async def test_save_answer_writes_markdown(cfg, conn):
    db.add_message(conn, "assistant", "Buck wins at light load.", screen="## Buck vs boost\n...")
    await run(make_ctx(cfg, conn), call("save_answer", title="Buck vs boost"))
    [it] = artifacts.items(conn)
    assert it["title"] == "Buck vs boost" and it["kind"] == "document"
    assert Path(it["path"]).read_text().startswith("# Buck vs boost")


async def test_save_answer_after_web_search_is_research(cfg, conn):
    db.add_message(conn, "assistant", "Found it.", actions='[{"name": "web_search"}]')
    await run(make_ctx(cfg, conn), call("save_answer", title="Found"))
    assert artifacts.items(conn)[0]["kind"] == "research"


def test_glance_has_bell_for_reminder_within_an_hour(cfg, conn):
    soon = (datetime.now() + timedelta(minutes=20)).isoformat(timespec="minutes")
    later = (datetime.now() + timedelta(hours=3)).isoformat(timespec="minutes")
    conn.execute("INSERT INTO reminders(at, text) VALUES(?,?),(?,?)", (soon, "call mom", later, "gym"))
    bells = [g for g in artifacts.glance(cfg, conn) if g["icon"] == "bell"]
    assert [b["detail"] for b in bells] == ["call mom"] and bells[0]["at"] == soon


def test_glance_leaves_out_missing_sources(cfg, conn, monkeypatch):
    monkeypatch.setattr(briefing, "weather_short", lambda cfg: (_ for _ in ()).throw(OSError()))
    assert all(g["icon"] != "sun" for g in artifacts.glance(cfg, conn))


def test_glance_sun_and_tasks(cfg, conn):
    conn.execute("INSERT INTO tasks(title, due) VALUES('Lab report', '')")
    g = {x["icon"]: x for x in artifacts.glance(cfg, conn)}
    assert g["sun"]["value"] == "31°" and g["check"]["detail"] == "Lab report"


def test_weather_short(cfg, monkeypatch):
    monkeypatch.setattr(briefing, "_weather_json", lambda cfg: {
        "temperature_2m_max": [33.4], "weather_code": [61]})
    assert real_weather_short(cfg) == ("33°", "Light rain")
