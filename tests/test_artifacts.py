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


def test_items_reads_only_a_short_preview_and_only_for_rows_that_make_the_cut(cfg, conn, tmp_path, monkeypatch):
    big = tmp_path / "big.md"
    big.write_text("x" * 5000)
    for name in ("a.md", "b.md"):
        (tmp_path / name).write_text(name)
        db.log_action(conn, "create_file", {"path": str(tmp_path / name)}, "ok", "done", None, None)
    db.log_action(conn, "create_file", {"path": str(big)}, "ok", "done", None, None)
    read = []
    real = artifacts._preview
    monkeypatch.setattr(artifacts, "_preview", lambda p: read.append(p.name) or real(p))
    out = artifacts.items(conn, n=2)
    assert [i["title"] for i in out] == ["big.md", "b.md"] and len(out[0]["preview"]) == 300
    assert sorted(read) == ["b.md", "big.md"]


def test_get_returns_one_artifact_without_preview_and_rejects_other_actions(cfg, conn, tmp_path):
    f = tmp_path / "a.md"
    f.write_text("hi")
    aid = db.log_action(conn, "create_file", {"path": str(f)}, "ok", "done", None, None)
    other = db.log_action(conn, "remind", {}, "ok", "done", None, None)
    assert artifacts.get(conn, aid)["exists"] is True and "preview" not in artifacts.get(conn, aid)
    assert artifacts.get(conn, other) is None and artifacts.get(conn, 999) is None


def test_commit_with_a_bare_project_name_does_not_exist(cfg, conn):
    db.log_action(conn, "ask_claude", {"task": "x", "project": "."}, "Commits in x:\nabc msg",
                  "done", None, None)
    assert artifacts.items(conn)[0]["exists"] is False


def test_glance_asks_for_the_weather_once_per_ttl(cfg, conn, monkeypatch):
    calls = []
    monkeypatch.setattr(briefing, "weather_short", lambda cfg: calls.append(1) or ("1°", "x"))
    artifacts.glance(cfg, conn)
    artifacts.glance(cfg, conn)
    assert len(calls) == 1


def test_commit_whose_path_is_a_file_does_not_exist(cfg, conn, tmp_path):
    (tmp_path / "f").write_text("x")
    db.log_action(conn, "ask_claude", {"task": "x", "project": str(tmp_path / "f")},
                  "Commits in f:\nabc msg", "done", None, None)
    assert artifacts.items(conn)[0]["exists"] is False
