# JARVIS Phase 2a: Tools, Laptop Control, Memory and Delegation — Design

Status: design parts 1–2 approved by the user in chat on 2026-10-11; part 3 (data,
errors, testing, demo) and this written spec were approved under the user's grant of
full authority for the session ("make decisions, list them, brief me"). Decisions
made without the user are listed in section 13.

## 1. Goal

JARVIS can act on the laptop, on command: open apps, files and folders; find files by
name; manage windows; run approved scripts; remember things; and hand hard or private
sub-tasks to a sub-agent. Anything risky asks first, by voice or button. Every action
is logged in plain language, with undo where possible.

## 2. Scope

In:
- Native tool calling through the existing `openai` SDK on all three routes.
- A tool loop in the brain (at most 5 model steps per turn).
- Confirmation by voice ("yes"/"no", 8 s, no wake word) or by button.
- An action log with undo (UI panel, Undo button, "undo that").
- Tools: `open_app`, `open_path`, `find_files`, `list_windows`, `focus_window`,
  `minimise_window`, `maximise_window`, `snap_window`, `close_window`, `run_script`,
  `remember`, `forget`, `undo_last`, `delegate`.
- Sub-agents `thinker` and `private`, defined in `config.toml`.

Out (with the phase that takes it):
- Reading file contents, the content index, embeddings, Q&A over notes and PDFs → 2b.
- Web search and page reading, the `researcher` agent → 2b.
- Accounts, briefing → 3. Remote access → 4. Hardware → 5.

## 3. Architecture

```
 brain.run_turn ──► llm.stream_reply(tools=…) ──► ("text", …) / ("tool_calls", [...])
      │  loop ≤ 5 steps
      ▼
 tools.run(ctx, call) ──► confirm? ──► fn(ctx, **args) ──► actions table (+ undo)
      │                     │
      │                     └─► hub.confirm(): UI card [Confirm][Cancel]
      │                                        + spoken question, 8 s voice yes/no
      └─► delegate(agent, task) ──► brain.agent_loop with the agent's route,
                                    prompt and tool subset; one level deep
```

| File | Job |
|---|---|
| `jarvis/tools/__init__.py` | `Tool` record, `TOOLS` registry, `schemas(names)`, `run(ctx, call)`, `undo(conn, action_id)`, `parse_yes_no(text)` |
| `jarvis/tools/apps.py` | `open_app`, `open_path`, `run_script` |
| `jarvis/tools/windows.py` | Win32 window helpers via `ctypes` + the window tools |
| `jarvis/tools/files.py` | Filename index (build/refresh) + `find_files` |
| `jarvis/tools/memory.py` | `remember`, `forget`, prompt block of memories |
| `jarvis/tools/delegate.py` | `delegate` |
| `jarvis/scripts/` | User-approved scripts (`.py`, `.ps1`, `.bat`); only these can run |

Changed: `llm.py` (tools, tool-call accumulation, `fallback` flag), `brain.py` (tool
loop, `agent_loop`, memory in system prompt, actions in history), `db.py` (tables),
`config.py` + `config.toml` (agents, allowed roots, persona), `voice.py` (CONFIRMING
state), `app.py` (confirm, undo, actions messages), `ui/index.html` (confirm card,
actions panel), `__main__.py` (start the file indexer).

No new dependencies: Win32 through `ctypes`, fuzzy matching through `difflib`.

## 4. Tool framework

- A tool is `Tool(name, fn, schema, confirm: bool, describe)`. `fn(ctx, **args)`
  returns `(result_text, undo)` where `undo` is a JSON-able dict or `None`.
  `describe(args)` returns the confirmation question ("Close Chrome, sir?").
- `ctx` carries `cfg`, `conn`, `confirm` (async callable), `emit`, `agent`
  (`None` for JARVIS itself, else the sub-agent's name), `route`, `prepare`/`speak`.
- `run(ctx, call)` order: unknown tool or bad JSON → error result; tool not allowed for
  this agent → error result; confirmation if `tool.confirm` or `ctx.agent` is set;
  declined → log `declined`, result `"The user declined."`; else run `fn` in a thread
  (`delegate` runs on the loop), log `done` or `failed`, return the result text
  (capped at 4000 characters).
- Undo kinds: `close_windows` (hwnds), `placement` (hwnd + saved placement),
  `focus` (previous foreground hwnd), `delete_memory` (id), `restore_memory` (id, text).
  `undo(conn, id)` applies it and marks the action `undone`. `undo_last` undoes the
  latest `done` action that has undo info.

## 5. The tool loop (brain)

```
messages = system(persona + memories) + history + user
for step in 1..5:
    stream reply with TOOLS schemas → text goes through the Splitter as today;
    collect tool calls
    no tool calls → done
    append assistant message (content, tool_calls); for each call append the
    tool result message (role "tool", tool_call_id)
after 5 steps with tool calls still coming → say "I've stopped after five steps, sir."
```

- Each step is a fresh `Splitter`; the spoken-sentence count (max 2) is per turn.
- Each step falls back across routes independently (existing behaviour).
- The assistant row saved to `messages` gains an `actions` column: one line per
  action ("open_app Chrome → done"). `_history` adds it to the content as
  `(actions: …)` so the model knows what it already did.
- Persona prompt: drop "You cannot act on the computer yet"; add: use tools when asked
  to act; at most one short sentence before acting; never claim success unless the tool
  result says so; delegate hard multi-step reasoning to `thinker` and anything about the
  user's files that should stay private to `private`; never delegate to do something the
  user did not ask for.

## 6. Tools

| Tool | Args | Confirm | Undo |
|---|---|---|---|
| `open_app` | `name` | no | close the new windows that appear within 3 s |
| `open_path` | `path` (inside allowed roots) | no | same |
| `find_files` | `query`, `limit` (≤ 20) | no | – |
| `list_windows` | – | no | – |
| `focus_window` | `window` | no | refocus the previous foreground window |
| `minimise_window` / `maximise_window` | `window` | no | restore placement |
| `snap_window` | `window`, `side` (`left`/`right`) | no | restore placement |
| `close_window` | `window` | **yes** | – |
| `run_script` | `name` | **yes** | – |
| `remember` | `text` | no | delete it |
| `forget` | `memory_id` | no | re-add it |
| `undo_last` | – | no | – |
| `delegate` | `agent`, `task` | no (its tools confirm) | – |

- **Apps:** at startup, `Get-StartApps` (PowerShell, ~2 s, in the indexer thread) lists
  name → AppID for Start-menu and Store apps. `open_app` fuzzy-matches the name
  (`difflib.get_close_matches`, cutoff 0.6, plus substring match) and launches
  `shell:AppsFolder\<AppID>` with `os.startfile`. No match → result lists the 5 closest.
- **Paths:** `open_path` resolves the path and refuses anything outside the allowed
  roots or with an executable extension (`.exe .bat .cmd .ps1 .vbs .js .msi .lnk .py
  .com .scr .url`) — those go through `run_script`. Opens with `os.startfile`.
- **Windows:** `window` is fuzzy-matched against visible top-level window titles and
  their process names (`chrome.exe` → "chrome"). Ambiguous → result lists candidates
  and nothing happens. Placement saved/restored with `GetWindowPlacement` /
  `SetWindowPlacement`; snap uses the monitor work area; focus uses the Alt-key trick
  so `SetForegroundWindow` is allowed; close posts `WM_CLOSE` (the app can still ask
  to save).
- **Scripts:** files in `jarvis/scripts/` with `.py` (current Python), `.ps1`
  (`powershell -NoProfile -ExecutionPolicy Bypass -File`) or `.bat` (`cmd /c`). Exact
  file name or stem match only. 60 s timeout; result = exit code + last 2000 characters
  of output. No arguments in 2a.
- **Files:** `files(path, name, mtime)` table. Built at startup and every 30 minutes by
  a background thread on its own SQLite connection (WAL mode), walking the allowed
  roots and skipping excluded directory names. `find_files` splits the query into words;
  every word must appear in the file name (case-insensitive); newest first.
- **Memory:** `memories(id, ts, text)`. Up to 50 newest go into the system prompt as
  `[id] text` lines under "Things the user asked you to remember:".

## 7. Delegation

```toml
[agents.thinker]
route = "reasoning"
fallback = true
tools = []
prompt = "You are a careful analyst working for JARVIS. Think step by step and return a clear, complete answer in markdown. You are not speaking to the user."

[agents.private]
route = "local"
fallback = false        # private work never leaves the laptop
tools = ["find_files", "open_path"]
prompt = "You are JARVIS's private file assistant. You run locally. Use your tools to answer and return a concise markdown answer."
```

- `delegate(agent, task)` runs `brain.agent_loop(ctx', [system: agent prompt, user:
  task], route, tool names)` — the same ≤ 5-step loop, text collected rather than
  spoken. `ctx'.agent` = agent name, so every tool it calls asks for confirmation,
  phrased "The private agent wants to open notes.pdf. Allow, sir?".
- `delegate` is never in an agent's tool list (one level deep, enforced in `run`).
- The UI status shows "Consulting thinker…" (`{"type":"agent","agent":"thinker"}`).
- The agent's answer is the tool result for JARVIS, which then answers the user.
- `fallback = false` → `stream_reply(..., fallback=False)` tries only that route.

## 8. Confirmation

- `hub.confirm(text) -> bool`: creates a future and an id, broadcasts
  `{"type":"confirm","id","text"}`, then, if voice is up, speaks the question and sends
  `confirm_listen` to the voice loop. Resolved by the first of:
  - UI `{"type":"confirm_reply","id","ok"}`;
  - voice `confirm_heard` text → `parse_yes_no` (yes words: yes, yeah, yep, sure, do it,
    go ahead, confirm, okay, ok; no words: no, nope, cancel, stop, don't, abort;
    "no" wins if both; neither → no);
  - voice silence for 8 s → no; a cancel, new typed text or barge-in → no
    (the turn is cancelled anyway);
  - without voice, 60 s with no click → no.
- Then broadcast `{"type":"confirm_done","id","ok"}`; on "no" by voice JARVIS says
  "Cancelled, sir." through the normal reply (the model gets "The user declined.").

Voice state machine additions (`C` = CONFIRMING):

| From | Event | To | Actions |
|---|---|---|---|
| T, S | `confirm_listen` | C | chime, start_buffer |
| C | `vad_end`, `hotkey_up` | T | transcribe_confirm |
| C | `timeout` / silence cap (8 s) | T | confirm_silent |
| C | `hotkey_down` | C | start_buffer |
| C | `cancel`, `text_input` | as today (generic rule) | stop_audio |

Wake-word detection is off in C (the answer itself might be mistaken for it).
`transcribe_confirm` emits `confirm_heard {text}` instead of `transcript`;
`confirm_silent` emits `confirm_heard {text: ""}`.

## 9. Data

```sql
ALTER TABLE messages ADD COLUMN actions TEXT DEFAULT '';   -- if missing
CREATE TABLE actions (id INTEGER PRIMARY KEY, ts TEXT, tool TEXT, args TEXT,
  result TEXT, status TEXT, undo TEXT, agent TEXT);
CREATE TABLE memories (id INTEGER PRIMARY KEY, ts TEXT, text TEXT);
CREATE TABLE files (path TEXT PRIMARY KEY, name TEXT, mtime REAL);
CREATE INDEX files_name ON files(name);
```

`connect` turns on WAL so the indexer's connection and the main one coexist.

Config additions:

```toml
[files]
roots = ["~"]
exclude = [".venv", "venv", "node_modules", ".git", "AppData", "__pycache__",
           "site-packages", ".cache"]
```

## 10. UI

- Confirmation card above the input: question text, **Confirm** and **Cancel** buttons,
  a countdown; removed on `confirm_done`.
- **Actions** panel (a `<details>` under the header, closed by default): newest first,
  one line each — time, tool, short args, status icon (✓ done, ✗ failed, – declined,
  ↶ undone), agent tag if any, and an **Undo** button when undo is possible.
  Messages: `actions` (last 30, on connect) and `action` (one row, insert or update).
  Undo button sends `{"type":"undo","id"}`.
- Status shows "Consulting thinker…" on `agent` messages.

## 11. Errors

| Situation | Behaviour |
|---|---|
| Tool raises | Logged `failed`; error text returned to the model; never retried automatically |
| Unknown tool / bad JSON arguments | Error result to the model; nothing runs |
| Model keeps calling tools | Stops after 5 steps, says so |
| Route can't do tools (API error on `tools`) | Existing fallback to the next route |
| Undo target gone (window closed) | Action stays `done`, banner "Couldn't undo: …" |
| Indexer fails | Banner once; `find_files` says the index is unavailable |
| `Get-StartApps` fails | `open_app` falls back to a direct `os.startfile(name)` attempt |

## 12. Testing

Unit tests (pytest, no network): tool registry and `run` (confirm/declined/failed,
agent restrictions, one-level delegation, result cap); `parse_yes_no`; undo for each
kind with fakes; `stream_reply` tool-call accumulation (split deltas, missing ids,
tool-only replies count as started); the brain loop with a fake stream (tool step then
text, 5-step cap, actions saved to history); memory tools and prompt block; filename
index on a temp tree with excluded dirs; `open_app` matching on a fake app list;
`open_path` refusals; `run_script` on a temp `.py`; FSM confirm transitions; hub
confirm resolved by button, by voice text, by timeout and by cancel; WebSocket `undo`.
Win32 helpers get a smoke test (list windows returns titles) because tests run on
Windows.

## 13. Decisions made without the user (session authority, 2026-10-11)

1. Part 3 of the design and this spec approved under delegated authority.
2. Apps come from `Get-StartApps` (covers Store apps like WhatsApp), not Start-menu
   `.lnk` scanning.
3. `open_path` refuses executable/script extensions so "open" can never run code.
4. The `private` agent never falls back to cloud routes (`fallback = false`).
5. The approved "anything a sub-agent asks to do needs confirmation" rule is applied
   literally, so even the private agent's `find_files` asks first. Easy to relax later.
6. Without a working mic, confirmations wait 60 s for a click, then count as "no".
7. Scripts take no arguments in 2a.
8. Undo for opened apps closes windows that appeared within 3 s of launching.
9. `.claude` and other dot-folders in home are indexed (only the listed names are
   excluded), matching the approved folder list.
10. Wake word is ignored while CONFIRMING.

## 14. Demo (ships in README)

1. "Hey Jarvis, open Notepad." → opens; Actions shows ✓ with Undo.
2. "Snap it to the left." → snapped. "Undo that." → back where it was.
3. "Find my files with motor in the name." → list on screen.
4. "Remember that my lab is on Thursdays." → restart JARVIS → "When is my lab?"
5. "Close Notepad." → "Close Notepad, sir?" → say "yes" → closed.
6. Put `hello.py` in `jarvis/scripts/`; "Run the hello script." → say "no" →
   "Cancelled"; again, click Confirm → output on screen.
7. "Think carefully with the thinker: plan my week for two exams and a lab report." →
   "Consulting thinker…" → answer.
8. "Ask the private agent which PDFs I have about motors." → it asks to search →
   confirm → answer, all on the local model.

## 15. Running cost

₹0 a month. No new services; Groq and Gemini free tiers plus local Ollama.
Tool calls add tokens (schemas ~1.5k tokens per request), still well inside free limits.
