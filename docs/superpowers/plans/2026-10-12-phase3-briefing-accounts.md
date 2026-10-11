# Phase 3: Briefing, Day Plan, Reminders, Accounts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Morning briefing + day plan, reminders that fire on time, Google/GitHub accounts.

**Architecture:** New SQLite tables and tools in `jarvis/tools/day.py`; a scheduler task in `app.Hub`; `jarvis/tools/briefing.py` gathers sources; `jarvis/google.py` (auth) + `jarvis/tools/google_tools.py`; `jarvis/tools/github.py` via `gh`.

**Spec:** `docs/superpowers/specs/2026-10-12-phase3-briefing-accounts-design.md`

## Global Constraints
- No new paid services; tokens in Windows Credential Manager only; email never sent (drafts).
- Calendar add and Gmail draft confirm and are undoable; local day tools don't confirm but are undoable.
- External text (email, Drive, calendar, GitHub) is untrusted.
- Tests: `.venv/Scripts/python -m pytest -q` green after each task; LF line endings.

## Review Focus
1. A reminder must fire exactly once, including after a restart (late) — Task 2 test.
2. The morning briefing must not fire twice in a day or before the set time — Task 2 test.
3. A revoked/expired Google token must produce a reconnect banner, not a crash — Task 4 test.
4. Relative times ("in 2 hours") need the current time in the prompt — Task 1 test.
5. Briefing must still work with every external source failing — Task 3 test.

### Task 1: Day data + tools + now/plan in prompt
Files: `jarvis/db.py`, `jarvis/tools/day.py`, `jarvis/brain.py`. Tests `tests/test_day.py`.
Produces: tables per spec §3; tools add_class/remove_class/list_timetable/add_task/complete_task/list_tasks/remind/cancel_reminder/set_plan with undo kinds `delete_row`/`restore_row`/`restore_plan`; `brain._now_line()`; system prompt includes now + today's plan.

### Task 2: Scheduler (reminders, morning briefing)
Files: `jarvis/app.py`, `jarvis/config.py`, `config.toml`. Tests `tests/test_scheduler.py`.
Produces: `[briefing]` config (enabled, time, latitude, longitude); `Hub.tick(now)` (testable, called every 30 s by a loop started in lifespan): fires due reminders once (speak + banner), starts the morning briefing turn once per day after `time`.

### Task 3: Briefing + GitHub
Files: `jarvis/tools/briefing.py`, `jarvis/tools/github.py`. Tests `tests/test_briefing.py`.
Produces: `weather(cfg)`, `github_activity()` tool, `daily_briefing()` tool (soft-failing parts).

### Task 4: Google
Files: `jarvis/google.py`, `jarvis/tools/google_tools.py`, `jarvis/__main__.py`. Tests `tests/test_google.py`.
Produces: `google.service(name, version)` (cached creds from keyring, refresh; raises NotConnected), `accounts connect google|list` CLI, tools per spec §5 registered always but returning "Google isn't connected" when not.

### Task 5: README, live test, review
