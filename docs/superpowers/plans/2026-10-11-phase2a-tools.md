# Phase 2a: Tools, Laptop Control, Memory and Delegation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** JARVIS calls tools (apps, files, windows, scripts, memory, delegation) with voice/button confirmation, an action log and undo.

**Architecture:** Native OpenAI-style tool calling through the existing `openai` SDK; a ≤5-step loop in `brain.run_turn`; tools are plain functions in `jarvis/tools/` registered in one dict; confirmation is an awaitable on the hub that the UI or the voice loop resolves.

**Tech Stack:** Python 3.13, openai SDK, FastAPI, SQLite, `ctypes` (Win32), `difflib`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-11-phase2a-tools-design.md`

## Global Constraints

- No new pip dependencies.
- Address the user as "sir"; never their name.
- File access read-only, limited to `[files].roots` minus `[files].exclude`.
- `close_window`, `run_script`, and any tool called by a sub-agent need confirmation.
- Max 5 model steps per turn; tool result text capped at 4000 characters.
- Voice confirmation window 8 s; button-only window 60 s; silence/unclear = no.
- Tests: `.venv/Scripts/python -m pytest -q` from the repo root, all green after each task.

## Review Focus

1. A model that emits tool-call deltas split across chunks, or without `id`/`index` (Gemini) — calls must still be assembled correctly. (Task 2 test)
2. A turn cancelled (barge-in / new text) while a confirmation is pending — the future must resolve "no" and nothing runs. (Task 7 test)
3. `open_path` given `..` traversal or a symlink outside the roots — refused. (Task 4 test)
4. Fuzzy window name matching two windows — nothing happens, candidates returned. (Task 5 test)
5. A sub-agent trying to call `delegate` or a tool outside its list — refused with an error result. (Task 3 test)

---

### Task 1: Data and config

**Files:** Modify `jarvis/db.py`, `jarvis/config.py`, `config.toml`; Test `tests/test_db.py`, `tests/test_config.py` (new)

**Interfaces — Produces:**
- `db.connect` enables WAL, creates `actions`, `memories`, `files` tables and the `files_name` index, adds `messages.actions` if missing.
- `db.add_message(conn, role, content, screen="", route="", interrupted=False, actions="") -> int`; `recent_messages` rows include `actions`.
- `db.log_action(conn, tool, args: dict, result, status, undo: dict|None, agent: str|None) -> int`
- `db.update_action(conn, id, **fields)`; `db.get_action(conn, id) -> dict|None`; `db.recent_actions(conn, n=30) -> list[dict]` (newest first; `args`/`undo` decoded from JSON)
- `db.add_memory(conn, text) -> int`; `db.delete_memory(conn, id) -> str|None` (returns deleted text); `db.restore_memory(conn, id, text)`; `db.memories(conn, n=50) -> list[dict]`
- `Config.agents: dict[str, Agent]` with `Agent(name, route, fallback: bool, tools: list[str], prompt)`; `Config.file_roots: list[Path]` (expanded `~`); `Config.file_exclude: list[str]`.

- [ ] Step 1: tests — `test_action_log_roundtrip` (log, update status → `recent_actions` returns decoded dict with status), `test_memory_add_delete_restore`, `test_messages_actions_column` (add with actions="x" → recent row has "x"), `test_old_db_gets_actions_column` (create a db with the Phase 1 messages schema, `connect` → column exists), `test_config_agents_and_files` (thinker route "reasoning", private fallback False and tools `["find_files","open_path"]`, roots[0] == Path.home()).
- [ ] Step 2: run → FAIL.
- [ ] Step 3: implement; add `[agents.*]` and `[files]` from spec §7/§9 to `config.toml`; update persona per spec §5.
- [ ] Step 4: run full suite → PASS. Step 5: commit `feat: action log, memories, file index tables; agents config`.

### Task 2: Tool calls in `llm.stream_reply`

**Files:** Modify `jarvis/llm.py`; Test `tests/test_llm.py`

**Interfaces — Produces:** `stream_reply(cfg, conn, messages, route, client_factory=make_client, tools=None, fallback=True)` yields `("route", name)`, `("text", delta)`, and at the end at most one `("tool_calls", [{"id", "name", "arguments": str}])`. `tools` passed through to `create(...)` only when non-empty. A tool-call delta counts as "started". `fallback=False` → only `route`. Token estimate uses `str(m.get("content") or "")`.

- [ ] Step 1: tests with a fake client: `test_tool_call_assembled_from_split_deltas` (name in chunk 1, args `'{"na'` + `'me":"x"}'` in chunks 2–3 → one call, arguments `'{"name":"x"}'`); `test_tool_call_missing_id_gets_one` (id None, index None → id non-empty, `index` treated as 0); `test_tool_only_reply_is_started` (no text, one call → yields route then tool_calls, no fallback); `test_no_fallback_flag` (first route errors, fallback=False → AllRoutesFailed, second route never called); `test_tools_not_sent_when_none`.
- [ ] Step 2: FAIL. Step 3: implement (accumulate by `index if not None else 0`). Step 4: PASS. Step 5: commit `feat: streamed tool calls in llm`.

### Task 3: Tool framework, memory tools, undo

**Files:** Create `jarvis/tools/__init__.py`, `jarvis/tools/memory.py`; Test `tests/test_tools.py`

**Interfaces — Produces:**
- `@dataclass Tool(name, fn, schema: dict, confirm: bool = False, describe: Callable[[dict], str] = ...)`; `TOOLS: dict[str, Tool]`; `register(tool)`.
- `@dataclass Ctx(cfg, conn, confirm: Callable[[str], Awaitable[bool]], emit, agent: str|None = None, allowed: list[str]|None = None, actions: list[str] = field(default_factory=list))`. `ctx.actions` collects "tool summary → status" lines for history.
- `schemas(names: list[str]) -> list[dict]` (OpenAI `{"type":"function","function":{...}}`).
- `async run(ctx, call: dict) -> str` per spec §4 (async tool fns are awaited directly; sync ones via `asyncio.to_thread`). Emits `{"type":"action", **row}` after logging.
- `undo(conn, action_id) -> str` (raises `ValueError` with a readable message if not undoable); undo kinds `delete_memory`, `restore_memory` here; `close_windows`, `placement`, `focus` dispatch to `jarvis.tools.windows` (Task 5) via a lazy import.
- `parse_yes_no(text) -> bool` per spec §8.
- Memory tools `remember(text)`, `forget(memory_id)`; `undo_last()`; `memory_block(conn) -> str` (empty string when no memories).
- Tool fn signature: `fn(ctx, **args) -> tuple[str, dict|None]` (or async).

- [ ] Step 1: tests — `test_parse_yes_no` (cases: "yes please"→T, "go ahead"→T, "no"→F, "yes no"→F, "maybe"→F, ""→F); `test_run_unknown_tool`; `test_run_bad_json`; `test_confirm_declined_logs_declined_and_does_not_run`; `test_confirm_accepted_runs`; `test_failed_tool_logged_failed_with_error_text`; `test_result_capped_4000`; `test_agent_cannot_call_unlisted_tool_or_delegate` (Review Focus 5); `test_agent_call_always_confirms` (non-confirm tool, ctx.agent="private" → confirm called with "The private agent wants to …"); `test_remember_then_undo_deletes`; `test_forget_then_undo_restores`; `test_undo_last_picks_latest_undoable`; `test_memory_block_lists_ids`.
- [ ] Step 2: FAIL. Step 3: implement. Step 4: PASS. Step 5: commit `feat: tool framework, memory tools, undo`.

### Task 4: Apps, paths, scripts, file index

**Files:** Create `jarvis/tools/apps.py`, `jarvis/tools/files.py`, `jarvis/scripts/.gitkeep`; Test `tests/test_apps.py`, `tests/test_files.py`

**Interfaces — Produces:**
- `files.build_index(db_path, roots, exclude) -> int` (own connection; replaces table contents in one transaction; returns count). `files.start_indexer(cfg, on_banner)` → daemon thread: loads apps, builds index, repeats every 1800 s.
- `files.find_files(ctx, query, limit=10)` → markdown list of paths, newest first, `limit` clamped to 20.
- `apps.load_apps() -> dict[str, str]` (name → AppID via `Get-StartApps | ConvertTo-Json`); module-level `APPS` cache; `apps.match_app(name, apps) -> str|None`.
- `apps.open_app(ctx, name)`, `apps.open_path(ctx, path)`, `apps.run_script(ctx, name)`; `SCRIPTS_DIR = Path(__file__).parents[1] / "scripts"` (monkeypatchable); `apps.inside_roots(path, roots) -> bool`.
- Opening uses `windows.new_windows_after(fn, wait=3.0) -> list[int]` from Task 5; until Task 5 exists, tests monkeypatch it.

- [ ] Step 1: tests — `test_index_skips_excluded_dirs` (tmp tree with `node_modules/a.txt`, `notes/motor.pdf` → only motor.pdf); `test_find_files_all_words_newest_first`; `test_match_app_fuzzy` ({"Google Chrome":"Chrome","Notepad":"..."}: "chrome"→Chrome, "notpad"→Notepad, "xyz"→None); `test_open_path_refuses_outside_roots_and_traversal` (Review Focus 3: `root/../secret.txt` refused); `test_open_path_refuses_executables` (`.bat`, `.lnk`); `test_run_script_py` (tmp scripts dir, `hi.py` prints "hi" → result contains "exit 0" and "hi"); `test_run_script_unknown_lists_available`; tool registry has `run_script.confirm is True`.
- [ ] Step 2: FAIL. Step 3: implement (`os.startfile` monkeypatched in tests). Step 4: PASS. Step 5: commit `feat: app, path, script and file-search tools`.

### Task 5: Window tools (Win32 via ctypes)

**Files:** Create `jarvis/tools/windows.py`; Test `tests/test_windows.py`

**Interfaces — Produces:** `list_top() -> list[dict(hwnd, title, exe)]`; `find_window(query, wins) -> int | list[dict]` (one hwnd, or candidate list when 0 or ≥2 match equally); `get_placement(hwnd) -> list[int]`/`set_placement(hwnd, data)`; `new_windows_after(fn, wait=3.0) -> list[int]`; `close_hwnds(hwnds)`; `restore_focus(hwnd)`; tools `list_windows`, `focus_window`, `minimise_window`, `maximise_window`, `snap_window(window, side)`, `close_window` (confirm, describe "Close {title}, sir?").

- [ ] Step 1: tests — `test_find_window_unique_by_exe`, `test_find_window_ambiguous_returns_candidates` (Review Focus 4: two "Chrome" titles → list), `test_find_window_none`; `test_list_top_smoke` (returns list of dicts with str titles); `test_snap_records_placement_undo` with `find_window`/`set_placement`/`get_placement`/`_move` monkeypatched; `test_close_window_requires_confirm`.
- [ ] Step 2: FAIL. Step 3: implement. Step 4: PASS. Step 5: commit `feat: window tools`.

### Task 6: Brain tool loop and delegation

**Files:** Modify `jarvis/brain.py`; Create `jarvis/tools/delegate.py`; Test `tests/test_brain.py`

**Interfaces:**
- Consumes: Task 2 `stream_reply(..., tools, fallback)`, Task 3 `Ctx`, `run`, `schemas`, `memory_block`.
- Produces: `run_turn(cfg, conn, user_text, ui_route, emit, speak, prepare=None, confirm=None)` (confirm defaults to an async fn returning False); `async agent_loop(ctx, messages, route, tool_names, fallback=True, on_text=None) -> str` (≤5 steps, returns full text); `JARVIS_TOOLS` = all registered except none; `MAX_STEPS = 5`; `STOPPED = "I've stopped after five steps, sir."`.
- `delegate(ctx, agent, task)` (async): unknown agent → error text; emits `{"type":"agent","agent":name}`; runs `agent_loop` with a child `Ctx(agent=name, allowed=agent.tools)`; returns text.

- [ ] Step 1: tests (fake `stream_reply` monkeypatched in brain) — `test_tool_step_then_text` (step 1 yields a `remember` call, step 2 text → memory stored, assistant row `actions` contains "remember"); `test_five_step_cap_says_stopped`; `test_history_includes_actions_line`; `test_memory_block_in_system_prompt`; `test_delegate_runs_agent_with_its_route_and_no_fallback` (private → route "local", fallback False); `test_delegate_unknown_agent`; existing brain tests still pass.
- [ ] Step 2: FAIL. Step 3: implement. Step 4: PASS. Step 5: commit `feat: tool loop and delegation in brain`.

### Task 7: Confirmation (voice + hub), undo and actions over WebSocket, UI, wiring, README

**Files:** Modify `jarvis/voice.py`, `jarvis/app.py`, `jarvis/ui/index.html`, `jarvis/__main__.py`, `README.md`; Test `tests/test_voice_states.py`, `tests/test_app.py`

**Interfaces:**
- Voice: `State.CONFIRMING = "confirming"`; table rows per spec §8; `_frame` skips wake word in C and uses `CONFIRM_CAP_S = 8`; actions `_do_transcribe_confirm`, `_do_confirm_silent` emit `confirm_heard`.
- Hub: `async confirm(text) -> bool`; `pending: dict[str, asyncio.Future]`; `from_thread("confirm_heard", {text})` resolves the oldest pending with `parse_yes_no`; `_stop_turn` resolves all pending with False; WS messages `confirm_reply`, `undo`; sends `actions` on connect; passes `confirm=hub.confirm` to the turn runner.
- `__main__` starts `files.start_indexer`.

- [ ] Step 1: tests — FSM: `test_confirm_listen_from_speaking`, `test_confirm_vad_end_transcribes_confirm`, `test_confirm_cancel_goes_idle`, `test_wake_ignored_in_confirming` (via `_frame` not needed: table has no (C,"wake")). App: `test_confirm_resolved_by_button`, `test_confirm_resolved_by_voice_text`, `test_confirm_times_out_no` (timeout monkeypatched to 0.1), `test_cancel_resolves_pending_confirm_false` (Review Focus 2), `test_undo_message_undoes_action`, `test_actions_sent_on_connect`. Update existing fakes to accept `**kw`.
- [ ] Step 2: FAIL. Step 3: implement, UI per spec §10, README demo per spec §14. Step 4: full suite PASS; launch app, screenshot UI. Step 5: commit `feat: confirmation, action log UI, undo; phase 2a complete`.
