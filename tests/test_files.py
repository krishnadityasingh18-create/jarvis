import os

from jarvis.tools import files, run
from tests.test_tools import call, make_ctx


def tree(tmp_path):
    tmp_path = tmp_path / "home"   # keep the test db (in tmp_path) out of the index
    tmp_path.mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "motor_lib.txt").write_text("x")
    (tmp_path / "notes").mkdir()
    old = tmp_path / "notes" / "Motor Fault old.pdf"
    new = tmp_path / "notes" / "motor_fault_new.pdf"
    other = tmp_path / "notes" / "lab.txt"
    for f in (old, new, other):
        f.write_text("x")
    os.utime(old, (1_000_000, 1_000_000))
    return old, new


def test_index_skips_excluded_dirs(cfg, conn, tmp_path):
    tree(tmp_path)
    n = files.build_index(cfg.db_path, [tmp_path / "home"], ["node_modules"])
    names = [r[0] for r in conn.execute("SELECT name FROM files")]
    assert n == 3 and "motor_lib.txt" not in names


async def test_find_files_all_words_newest_first(cfg, conn, tmp_path):
    old, new = tree(tmp_path)
    files.build_index(cfg.db_path, [tmp_path / "home"], ["node_modules"])
    r = await run(make_ctx(cfg, conn), call("find_files", query="motor FAULT"))
    assert r.index(str(new)) < r.index(str(old)) and "lab.txt" not in r


async def test_find_files_reindex_replaces(cfg, conn, tmp_path):
    old, new = tree(tmp_path)
    files.build_index(cfg.db_path, [tmp_path / "home"], ["node_modules"])
    new.unlink()
    files.build_index(cfg.db_path, [tmp_path / "home"], ["node_modules"])
    r = await run(make_ctx(cfg, conn), call("find_files", query="fault"))
    assert str(new) not in r and str(old) in r


async def test_find_files_like_wildcards_are_literal(cfg, conn, tmp_path):
    tree(tmp_path)
    files.build_index(cfg.db_path, [tmp_path / "home"], ["node_modules"])
    r = await run(make_ctx(cfg, conn), call("find_files", query="%"))
    assert "No files" in r


async def test_find_files_empty_index(cfg, conn):
    r = await run(make_ctx(cfg, conn), call("find_files", query="x"))
    assert "still being built" in r
