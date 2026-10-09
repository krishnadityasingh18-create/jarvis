# JARVIS Phase 1: Voice Loop and Personality — Design

Date: 2026-10-09 · Status: draft for review

## 1. Goal

A local assistant you can talk to. Say "Hey Jarvis", hold a hotkey, or type,
and it answers in the Kokoro `bm_lewis` voice: a short spoken reply, with any
longer detail shown on screen. You can interrupt it mid-sentence.

**Done when** the demo script in §10 passes on this laptop.

## 2. Scope

In scope:
- Wake word, push-to-talk hotkey, and a text box as three ways to give input
- Local speech-to-text and text-to-speech, streamed sentence by sentence
- Barge-in (interrupting it while it speaks)
- Personality, and the split between the spoken part and the on-screen part
- Model routing across Groq, Gemini and Ollama, with automatic fallback
- Conversation history that persists across restarts
- Usage counter, monthly spend cap, and a warning at 80%
- Desktop window showing status, transcript, detail pane, text box and usage meter

Out of scope (with the phase that takes it):
- Any tool or action, the action log, and confirmation prompts → Phase 2. This is a
  change from the approved plan: Phase 1 has no actions to log, so the log arrives
  with the first tool.
- Long-term memory ("remember that…") → Phase 2, built as a tool
- Remote access, auth tokens, Vapi and n8n → Phase 4
- Automatic task-based routing → only if manual route plus fallback proves insufficient

## 3. Architecture

A single Python process, started with `python -m jarvis`, bound to `127.0.0.1:8765`.

```
 mic ─► voice.py ──(text)──► brain ──► llm.py ──► Groq / Gemini / Ollama
         ▲   │                 │          │
 hotkey ─┘   ◄──(sentences)────┘          └─► db.py (usage)
             ▼                  │
          speaker             db.py (messages)
                                │
 Edge app window ◄── WebSocket ─┘ app.py (FastAPI: page + /ws)
```

Package layout, one module per concern:

| File | Job | Depends on |
|---|---|---|
| `jarvis/__main__.py` | Load config, start the server, voice thread and hotkey, then open the window | everything below |
| `jarvis/config.py` | Read `config.toml`; fetch secrets from `keyring` | tomllib, keyring |
| `jarvis/db.py` | SQLite: `messages` and `usage` tables, plus helpers | sqlite3 |
| `jarvis/llm.py` | `stream_reply(messages, route)`: routing, fallback, usage recording, cap enforcement | openai SDK, db |
| `jarvis/brain.py` | One turn: build the prompt, stream the LLM, split spoken from screen, emit events | llm, db |
| `jarvis/voice.py` | Mic, wake word, VAD, speech-to-text, text-to-speech, playback, and the state machine | openwakeword, faster-whisper, kokoro-onnx, sounddevice |
| `jarvis/app.py` | FastAPI: `GET /` serves the UI; `WS /ws` handles events in both directions | fastapi |
| `jarvis/ui/index.html` | The whole UI, a single file with vanilla JS | — |
| `config.toml` | Routes, voice, hotkey, budget, persona | — |

Threads: the voice loop runs in its own thread. The server runs on the asyncio
loop. The two communicate through one `asyncio.Queue` of events. The rule is
"a turn is a cancellable task": barge-in cancels the current turn's task, and
that stops the LLM stream and the playback together.

## 4. Voice loop

States: `IDLE → LISTENING → THINKING → SPEAKING → IDLE`. The current state is
pushed to the UI on every change.

- **IDLE:** the mic streams 16 kHz mono into openWakeWord (`hey_jarvis`,
  threshold in config, default 0.5). A wake-word hit, a hotkey press, or text
  input moves to LISTENING (text input jumps straight to THINKING).
- **LISTENING:** audio is buffered until Silero VAD reports `end_silence_ms`
  of silence (default 700) or the hotkey is released. A 15 s safety cap applies.
  A short chime plays on entry.
- **THINKING:** faster-whisper transcribes the buffer, then `brain` starts the
  turn. An empty transcript gets the reply "Sorry sir, I didn't catch that."
- **SPEAKING:** sentences arrive from `brain` and are synthesised by Kokoro
  (`bm_lewis`, speed 0.9) and played in order. Sentence N+1 is synthesised
  while sentence N plays.
- **Barge-in**, set by `barge_in` in config:
  - `"wake"` (the default, safe on laptop speakers): the wake word or hotkey
    during SPEAKING cancels the turn and moves to LISTENING.
  - `"voice"` (headphones only): any VAD speech of 300 ms or more during
    SPEAKING cancels the turn. Without echo cancellation, laptop speakers would
    trigger this on JARVIS's own voice, so it is not the default.
  - Clicking the orb in the UI also cancels the turn.
- An interrupted assistant message is saved with only the text actually spoken,
  plus the flag `interrupted=1`.

Models: Whisper `large-v3-turbo` on CUDA with `int8_float16`. It handles
Indian-accented English far better than `small.en`, and needs about 1.6 GB of
VRAM. Kokoro runs on `onnxruntime-gpu`. If no CUDA device is found, both fall
back to CPU.

**Latency target:** from the end of your speech to the first audio, under
1.5 s on the `fast` route.

## 5. Brain: personality and the spoken/screen split

The system prompt, editable in `config.toml`:

> You are JARVIS, the personal assistant of a second-year ECE student at SRM
> Chennai. Formal but warm, composed, quietly witty. Address the user as
> "sir"; never use their name. Your reply has two parts. First, what you say aloud:
> at most two short sentences, plain speech, no markdown, no lists, no URLs.
> If more detail would help, write a line containing only `---`, then the
> on-screen detail in markdown (derivations, code, lists, tables). Never read
> the detail aloud and never say "see the screen" more than once per reply.
> You cannot act on the computer yet; if asked, say that comes in a later phase.

Splitting while streaming: tokens accumulate. Before `---`, each completed
sentence (ending in `.`, `?`, `!` or a newline) goes to `voice` and to the UI
transcript. Everything after `---` goes to the UI detail pane only. If the model
skips `---` and the spoken part runs past 2 sentences, sentence 3 onward goes to
screen only, so it never rambles aloud.

Context sent each turn: the system prompt, the last 20 messages from `db`, and
the new user message.

## 6. Model routing

`config.toml`:

```toml
[routes.fast]       # default
base_url = "https://api.groq.com/openai/v1"
model    = "llama-3.3-70b-versatile"
key      = "groq"               # keyring entry name

[routes.reasoning]
base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
model    = "gemini-2.5-flash"
key      = "gemini"

[routes.local]
base_url = "http://localhost:11434/v1"
model    = "qwen3:4b"
key      = ""

[routing]
default  = "fast"
fallback = ["fast", "reasoning", "local"]
```

Model names are confirmed against each provider's current list at
implementation time. All three speak the OpenAI chat API, so `llm.py` is a
single function using the `openai` SDK with a different `base_url`. There are
no provider classes. Swapping a model is a config edit.

- **Route choice:** the UI has a dropdown (fast / reasoning / local), which
  persists. A spoken prefix such as "think carefully…" selects `reasoning`
  for that turn only.
- **Fallback:** on a connection error, 429 or 5xx before the first token, try
  the next route in `fallback`. A failure after streaming has started ends the
  turn with "I lost the connection, sir," and nothing is retried.
- **Privacy rule** (enforced in Phase 2 when files arrive): turns that carry
  file content may only use `local`.

## 7. Data (SQLite, `data/jarvis.db`)

```sql
CREATE TABLE messages (id INTEGER PRIMARY KEY, ts TEXT, role TEXT,
  content TEXT, screen TEXT, route TEXT, interrupted INTEGER DEFAULT 0);
CREATE TABLE usage (id INTEGER PRIMARY KEY, ts TEXT, provider TEXT,
  model TEXT, tokens_in INTEGER, tokens_out INTEGER, cost_inr REAL);
```

- **Spend:** `cost_inr = tokens × price` from `[prices]` in config. Free tiers
  are priced at 0, so today the counter shows tokens and requests per provider,
  and rupees stay at ₹0 until a paid route or Phase 4 calls exist.
- **Cap:** `budget.monthly_inr` (default 300). At 80% or more, the UI shows a
  banner and JARVIS says it once per day: "Sir, we've used 80 percent of this
  month's budget." At 100%, every route with a price above 0 is skipped, and
  fallback continues with free or local routes.

## 8. UI

`ui/index.html` opens with `msedge --app=http://127.0.0.1:8765` (no browser
chrome). It contains:

- A status orb: idle, listening, thinking or speaking, each a colour. Clicking
  it while it speaks cancels the turn.
- The transcript, made of spoken lines only
- The detail pane, rendered markdown of the latest reply's on-screen part, using a vendored `marked.min.js`
- A text box (Enter to send) and the route dropdown
- A usage meter: "₹0 / ₹300 · Groq 41 req today"
- A banner area for warnings: mic missing, budget, route fallbacks

WebSocket events, server to UI: `state`, `user_text`, `spoken_sentence`,
`screen_delta`, `turn_end`, `usage`, `banner`. UI to server: `text_input`,
`cancel`, `set_route`.

## 9. Errors

| Failure | Behaviour |
|---|---|
| No mic, or mic busy | Banner "Voice off, text only"; the text box keeps working; retry every 10 s |
| No CUDA | Fall back to CPU, with a banner warning that it will be slower |
| Ollama not running | The `local` route is skipped in fallback, with a banner |
| All routes fail | Spoken: "I can't reach any model right now, sir." The error goes to screen |
| TTS fails | Reply shows as text only; banner |
| Missing API key | Startup prints `python -m jarvis keys set groq` and keeps running with the routes that have keys |

Secrets are only ever read through `keyring`. `config.toml` stores key names,
not values. `data/` and any `.env` are in `.gitignore`.

## 10. Testing

Automated tests run with `pytest` and use no audio, network or GPU:

- `test_brain.py`: the spoken/screen splitter covering a normal reply, no
  `---`, `---` arriving mid-token, a long spoken part, and an interrupted turn
  that saves only the spoken text
- `test_llm.py`: fallback order on 429 and on connection error, no retry after
  the first token, paid routes skipped at the cap, usage rows written. The
  provider is faked by pointing the openai client at a stub.
- `test_db.py`: last-N history, monthly usage sum, the 80% check
- `test_voice_states.py`: the state machine driven by fake events (wake, VAD
  end, hotkey, barge-in in both modes, cancel)

Manual demo, the acceptance test:

1. `python -m jarvis` opens the window with the orb idle and the meter at ₹0.
2. Say "Hey Jarvis", then "good evening". You get a short spoken greeting
   addressed to "sir".
3. Say "Hey Jarvis, explain the Nyquist sampling theorem." It gives one or two
   spoken sentences, and the detail pane shows the derivation.
4. While it is speaking, say "Hey Jarvis" (or press the hotkey). It stops
   within about 300 ms and listens.
5. Hold the hotkey and ask "what did I just ask you about?" It answers using
   history.
6. Type "think carefully: compare BJT and MOSFET as switches." It uses the
   reasoning route, shown in the UI.
7. Disconnect Wi-Fi and ask something. It falls back to `local` with a banner.
8. Restart the app. The transcript reloads.

## 11. Setup (outline; full steps ship with the phase)

1. Create the environment with `uv` on Python 3.13. If any audio dependency lacks
   a 3.13 wheel, pin 3.12 for this project.
2. Install `pip-system-certs` (the Cloudflare WARP SSL issue on this laptop).
3. Install the CUDA runtime libraries for faster-whisper (`nvidia-cublas-cu12`,
   `nvidia-cudnn-cu12`).
4. Install Ollama, then `ollama pull qwen3:4b`.
5. Run `python -m jarvis keys set groq` and `… gemini` (free keys).
6. Optional: a shortcut in `shell:startup` to launch at login.

## 12. Running cost

₹0 a month. Groq and Gemini free tiers, with local Whisper, Kokoro and Ollama.

VRAM budget is roughly Whisper 1.6 GB, Kokoro 0.5 GB and qwen3:4b about 3 GB,
which is about 5 GB of 8. Upgrading to `qwen3:8b` would fit only just.
