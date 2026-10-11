# Phase 2b: File Contents, Reversible Writes and Web — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Q&A over the user's files on the local model, reversible file writes, and web search/reading with sources.

**Architecture:** A content index in SQLite (FTS5 + nomic-embed-text vectors) filled by the existing indexer thread; new tools in `jarvis/tools/notes.py`, `edit.py`, `web.py`; `Tool` gains `private`, `readonly`, `untrusted` flags that drive privacy routing and the tainted-turn confirmation rule.

**Tech Stack:** Python 3.13, SQLite FTS5, numpy, PyMuPDF, python-docx, python-pptx, ddgs, primp, trafilatura, Ollama (`nomic-embed-text`, `jarvis-local`).

**Spec:** `docs/superpowers/specs/2026-10-12-phase2b-files-web-design.md`

## Global Constraints

- No permanent operation: deletes and replaced versions go to `data/trash/<action id>/`; nothing erases.
- Writes: inside roots, not excluded, not inside the JARVIS folder, never `_runs_code` types, text ≤ 1 MB.
- `write_file`, `edit_file`, `move_path`, `delete_path` confirm; after a web/file-content tool, every non-read-only tool confirms.
- File contents reach only the `local` route while `content_private = true`.
- `read_file` ≤ 6000 characters per call; `read_page` ≤ 8000; `web_search` n ≤ 8.
- Tests: `.venv/Scripts/python -m pytest -q`, all green after each task. Files keep LF line endings.

## Review Focus

1. A private turn whose local model is down must not fall back to a cloud route. (Task 3 test)
2. Undo of a delete/move when something now occupies the original path must refuse, not overwrite. (Task 4 test)
3. `read_page` to `http://127.0.0.1:8765/...` or a hostname resolving to a private IP is refused. (Task 5 test)
4. Text in a file or page saying "call delete_path on …" must not run without confirmation. (Task 3 test)
5. A file that fails extraction (encrypted/corrupt PDF) must not stop the rest of the index pass. (Task 2 test)

---

### Task 1: Data, config, dependencies

Files: `jarvis/db.py`, `jarvis/config.py`, `config.toml`, `requirements.txt`, `Modelfile`, tests `tests/test_db.py`, `tests/test_config.py`.

Produces: `docs`, `chunks`, `chunks_fts` tables (triggers keep FTS in sync with `chunks`); `messages.private`; `db.add_message(..., private=False)`; `recent_messages` rows include `private`. `Config.content_exclude: list[str]`, `Config.content_private: bool`, `Config.app_dir: Path` (the repo folder), `agents.researcher`. `config.toml`: local model `jarvis-local`, persona citation rule + untrusted-content rule.

Tests: `test_chunks_fts_sync` (insert/delete chunk → FTS match count follows), `test_message_private_flag`, `test_config_content_settings`.

### Task 2: Extraction, chunking, index, search (`jarvis/notes.py`)

Produces:
- `extract(path: Path) -> list[tuple[int, str]]` (page, text); raises on unreadable.
- `chunk(pages, size=1000, overlap=150) -> list[tuple[int, str]]`.
- `embed(texts: list[str], kind: "document"|"query") -> np.ndarray | None` (Ollama `nomic-embed-text`; None if unavailable).
- `index_contents(db_path, roots, exclude, embed=embed) -> dict` (counts: added, updated, removed, failed, embedded); incremental on `(mtime, size)`.
- `search(conn, query, k=6, embed=embed) -> list[dict(path, page, text, score)]` (RRF of FTS and vectors; FTS-only when vectors missing).

Tests: `test_extract_pdf_docx_pptx_ipynb_txt` (generated in tmp), `test_chunk_overlap_and_pages`, `test_index_incremental` (add, change, delete, unchanged skipped), `test_index_survives_bad_pdf` (Review Focus 5), `test_index_skips_content_exclude`, `test_search_fuses_fts_and_vectors` (fake embedder: vector-only hit and FTS-only hit both returned), `test_search_without_vectors`.

### Task 3: File-content tools, privacy routing, taint

Files: `jarvis/tools/__init__.py`, `jarvis/tools/notes.py` (new), `jarvis/brain.py`, `jarvis/tools/delegate.py`.

Produces: `Tool.private`, `Tool.readonly`, `Tool.untrusted` flags; `Ctx.private`, `Ctx.tainted`; `run` sets them after a flagged tool and requires confirmation for any non-readonly tool when tainted; untrusted results wrapped `<untrusted source=…>`; `agent_loop` switches to `local`, `fallback=False` once `ctx.private`; run_turn saves `private`; `_history(conn, redact: bool)` redacts private turns; delegate to a no-fallback agent sets `ctx.private`. Tools `search_notes(query, k=6)`, `read_file(path, page=0)`.

Tests: `test_search_notes_returns_sources`, `test_read_file_guards_and_cap`, `test_private_tool_switches_turn_to_local_no_fallback` (Review Focus 1), `test_history_redacts_private_turns_for_cloud`, `test_tainted_turn_confirms_side_effects` (Review Focus 4), `test_readonly_tools_not_confirmed_when_tainted`, `test_private_agent_delegate_marks_turn_private`.

### Task 4: Reversible writes (`jarvis/tools/edit.py`)

Produces: tools per spec §5; `trash_dir(conn, action)`; undo kinds `trash_restore`, `restore_version`, `move_back`, `remove_empty_dir` handled in `tools.undo`; `can_write(path, cfg) -> Path` guard (raises).

Tests: one per tool + its undo; `test_undo_refuses_when_target_reoccupied` (Review Focus 2); guards: outside roots, excluded, JARVIS folder, `.bat`/`.lnk`, > 1 MB; `test_edit_requires_unique_snippet`; confirm flags.

### Task 5: Web tools (`jarvis/tools/web.py`) and researcher

Produces: `web_search(query, n=5)`, `read_page(url)`; `_safe_url(url)` (scheme + resolved-IP check); fetch via `primp.Client(impersonate="chrome")` (monkeypatchable `_fetch`).

Tests: `test_web_search_formats_results` (fake DDGS), `test_read_page_html_and_pdf` (fake fetch), `test_read_page_refuses_local_and_private` (Review Focus 3), `test_web_results_marked_untrusted`, researcher agent config.

### Task 6: Wiring, README, live test

`files.start_indexer` runs `notes.index_contents` after the filename pass (banner on first failure); README Phase 2b section (setup: `pip install -r requirements.txt`, `ollama pull nomic-embed-text`, `ollama create jarvis-local -f Modelfile`; demo per spec §10); live run against the real home folder.
