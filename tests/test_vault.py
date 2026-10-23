from pathlib import Path

import pytest

from jarvis import db, notes, tools
from jarvis.tools import run, vault
from tests.test_tools import call, make_ctx


@pytest.fixture(autouse=True)
def roots(cfg, tmp_path):
    cfg.file_roots = [tmp_path]       # tmp_path sits under AppData, which is excluded by default
    cfg.app_dir = tmp_path / "app"


async def test_keep_text_note(cfg, conn):
    r = await run(make_ctx(cfg, conn), call("keep", text="Lab locker number 214"))
    assert "Kept" in r
    [it] = vault.items(conn)
    assert it["kind"] == "note" and it["title"] == "Lab locker number 214"


async def test_keep_file_copies_and_leaves_original(cfg, conn, tmp_path):
    src = tmp_path / "resume.pdf"
    src.write_bytes(b"%PDF-1.4 x")
    await run(make_ctx(cfg, conn), call("keep", path=str(src)))
    [it] = vault.items(conn)
    assert src.exists() and Path(it["path"]).parent == vault.folder(cfg)
    assert Path(it["path"]).read_bytes() == b"%PDF-1.4 x"


async def test_keep_same_file_twice_is_one_item(cfg, conn, tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("x")
    await run(make_ctx(cfg, conn), call("keep", path=str(src)))
    r = await run(make_ctx(cfg, conn), call("keep", path=str(src)))
    assert "already in your vault" in r and len(vault.items(conn)) == 1


async def test_keep_over_100_mb_asks(cfg, conn, tmp_path, monkeypatch):
    src = tmp_path / "big.bin"
    src.write_bytes(b"0")
    monkeypatch.setattr(vault, "MAX_QUIET_BYTES", 0)
    ctx = make_ctx(cfg, conn, answer=False)
    r = await run(ctx, call("keep", path=str(src)))
    assert ctx.asked and vault.items(conn) == [] and "declined" in r


async def test_keep_link(cfg, conn):
    await run(make_ctx(cfg, conn), call("keep", url="https://espressif.com/s3.pdf",
                                        title="ESP32-S3 datasheet"))
    assert vault.items(conn)[0]["kind"] == "link"


async def test_remove_goes_to_trash_and_undo_restores(cfg, conn, tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("x")
    await run(make_ctx(cfg, conn), call("keep", path=str(src)))
    it = vault.items(conn)[0]
    await run(make_ctx(cfg, conn), call("remove_from_vault", id=it["id"]))
    assert vault.items(conn) == [] and not Path(it["path"]).exists()
    tools.undo(conn, db.recent_actions(conn, 1)[0]["id"])
    assert vault.items(conn)[0]["path"] == it["path"] and Path(it["path"]).exists()


async def test_undo_keep_removes_item_into_trash(cfg, conn):
    await run(make_ctx(cfg, conn), call("keep", text="x"))
    tools.undo(conn, db.recent_actions(conn, 1)[0]["id"])
    assert vault.items(conn) == []


async def test_find_in_vault_matches_title_and_text(cfg, conn):
    await run(make_ctx(cfg, conn), call("keep", text="Gift idea for mom: the brass lamp"))
    r = await run(make_ctx(cfg, conn), call("find_in_vault", query="lamp"))
    assert "brass lamp" in r


def test_vault_folder_is_content_indexed_despite_app_dir_skip(cfg, tmp_path):
    root = tmp_path / "app"
    v = root / "data" / "vault"
    v.mkdir(parents=True)
    (v / "n.md").write_text("hello")
    (root / "secret.md").write_text("no")
    found = [p for p, *_ in notes._walk([str(tmp_path), str(v)], [], [root])]
    assert any(p.endswith("n.md") for p in found) and not any(p.endswith("secret.md") for p in found)
