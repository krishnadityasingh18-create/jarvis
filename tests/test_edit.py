import pytest

from jarvis import db
from jarvis.tools import TOOLS, run, undo
from tests.test_tools import call, make_ctx


@pytest.fixture
def home(cfg, tmp_path):
    h = tmp_path / "home"
    (h / "notes").mkdir(parents=True)
    cfg.file_roots = [h]
    cfg.app_dir = tmp_path / "home" / "jarvis"
    (cfg.app_dir / "scripts").mkdir(parents=True)
    return h


def last(conn):
    return db.recent_actions(conn)[0]


def trash_files(cfg):
    t = cfg.db_path.parent / "trash"
    return sorted(p.name for p in t.rglob("*") if p.is_file()) if t.exists() else []


async def test_create_file_and_undo(cfg, conn, home):
    p = home / "notes" / "todo.md"
    r = await run(make_ctx(cfg, conn), call("create_file", path=str(p), content="a\nb\n"))
    assert r.startswith("Created") and p.read_text() == "a\nb\n"
    assert not TOOLS["create_file"].confirm
    undo(conn, last(conn)["id"])
    assert not p.exists() and trash_files(cfg) == ["todo.md"]      # undone into the trash


async def test_create_file_refuses_existing(cfg, conn, home):
    (home / "notes" / "x.md").write_text("keep")
    r = await run(make_ctx(cfg, conn), call("create_file", path=str(home / "notes" / "x.md"),
                                            content="new"))
    assert r.startswith("Error") and (home / "notes" / "x.md").read_text() == "keep"


async def test_write_file_confirms_backs_up_and_undo_restores(cfg, conn, home):
    p = home / "notes" / "x.md"
    p.write_text("old")
    ctx = make_ctx(cfg, conn)
    await run(ctx, call("write_file", path=str(p), content="new"))
    assert ctx.asked == ["Overwrite x.md, sir?"] and p.read_text() == "new"
    undo(conn, last(conn)["id"])
    assert p.read_text() == "old" and "x.md" in trash_files(cfg)   # the newer one kept too


async def test_edit_file_and_undo(cfg, conn, home):
    p = home / "notes" / "x.md"
    p.write_bytes(b"three tasks\r\nmore\r\n")
    await run(make_ctx(cfg, conn), call("edit_file", path=str(p), old="three tasks\nmore",
                                        new="four tasks\nmore"))
    assert p.read_bytes() == b"four tasks\r\nmore\r\n"           # line endings kept
    undo(conn, last(conn)["id"])
    assert p.read_bytes() == b"three tasks\r\nmore\r\n"


async def test_edit_requires_unique_snippet(cfg, conn, home):
    p = home / "notes" / "x.md"
    p.write_text("a a")
    r = await run(make_ctx(cfg, conn), call("edit_file", path=str(p), old="a", new="b"))
    assert "2 times" in r and p.read_text() == "a a"
    r = await run(make_ctx(cfg, conn), call("edit_file", path=str(p), old="zzz", new="b"))
    assert "not found" in r


async def test_create_folder_and_undo(cfg, conn, home):
    d = home / "notes" / "sem3" / "labs"
    await run(make_ctx(cfg, conn), call("create_folder", path=str(d)))
    assert d.is_dir()
    undo(conn, last(conn)["id"])
    assert not d.exists()


async def test_move_and_undo(cfg, conn, home):
    src, dst = home / "notes" / "a.md", home / "notes" / "sub" / "b.md"
    src.write_text("x")
    ctx = make_ctx(cfg, conn)
    await run(ctx, call("move_path", src=str(src), dst=str(dst)))
    assert ctx.asked == ["Move a.md to b.md, sir?"] and dst.read_text() == "x" and not src.exists()
    undo(conn, last(conn)["id"])
    assert src.read_text() == "x" and not dst.exists()


async def test_delete_goes_to_trash_and_undo_restores(cfg, conn, home):
    d = home / "notes" / "old"
    d.mkdir()
    (d / "f.txt").write_text("x")
    ctx = make_ctx(cfg, conn)
    await run(ctx, call("delete_path", path=str(d)))
    assert ctx.asked == ["Delete old (it goes to the JARVIS trash), sir?"]
    assert not d.exists() and trash_files(cfg) == ["f.txt"]
    undo(conn, last(conn)["id"])
    assert (d / "f.txt").read_text() == "x"


async def test_undo_refuses_when_target_reoccupied(cfg, conn, home):
    p = home / "notes" / "x.md"
    p.write_text("original")
    await run(make_ctx(cfg, conn), call("delete_path", path=str(p)))
    p.write_text("a new file with the same name")
    with pytest.raises(ValueError):
        undo(conn, last(conn)["id"])
    assert p.read_text() == "a new file with the same name"


@pytest.mark.parametrize("where", ["outside", "excluded", "jarvis", "bat", "lnk", "trash"])
async def test_write_guards(cfg, conn, home, tmp_path, where):
    target = {"outside": tmp_path / "x.md", "excluded": home / "node_modules" / "x.md",
              "jarvis": cfg.app_dir / "scripts" / "evil.py", "bat": home / "run.bat",
              "lnk": home / "a.lnk", "trash": cfg.db_path.parent / "trash" / "x.md"}[where]
    r = await run(make_ctx(cfg, conn), call("create_file", path=str(target), content="x"))
    assert r.startswith("Error") and not target.exists()


async def test_write_size_cap(cfg, conn, home):
    r = await run(make_ctx(cfg, conn), call("create_file", path=str(home / "big.txt"),
                                            content="x" * (2**20 + 1)))
    assert r.startswith("Error") and not (home / "big.txt").exists()


async def test_move_cannot_rename_into_a_program(cfg, conn, home):
    (home / "notes" / "a.txt").write_text("x")
    r = await run(make_ctx(cfg, conn), call("move_path", src=str(home / "notes" / "a.txt"),
                                            dst=str(home / "notes" / "a.bat")))
    assert r.startswith("Error") and (home / "notes" / "a.txt").exists()


def test_confirm_flags():
    assert all(TOOLS[n].confirm for n in ("write_file", "edit_file", "move_path", "delete_path"))
    assert not TOOLS["create_folder"].confirm
