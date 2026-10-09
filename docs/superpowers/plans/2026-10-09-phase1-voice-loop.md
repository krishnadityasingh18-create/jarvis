# JARVIS Phase 1: Voice Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local voice and text assistant. Wake word, hotkey or text → Whisper → LLM (routed, with fallback) → Kokoro voice, with barge-in, a spoken/screen split, and a usage meter in an Edge app window.

**Architecture:** One Python process. FastAPI on `127.0.0.1:8765` serves a single-file UI over a WebSocket. A voice thread runs mic → wake word → VAD → STT and plays TTS. Each conversational turn is one cancellable asyncio task, created by `brain.run_turn`, which streams from `llm.stream_reply`. SQLite holds messages and usage.

**Tech Stack:** Python 3.13, FastAPI + uvicorn, openai SDK (OpenAI-compatible endpoints for Groq, Gemini and Ollama), faster-whisper, openWakeWord (wake word and its bundled Silero VAD), kokoro-onnx, sounddevice, keyboard, keyring, pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-phase1-voice-loop-design.md`

## Global Constraints

- Python 3.13 in `.venv` (`python -m venv .venv`; `uv` is not installed). If an audio dependency has no cp313 wheel, recreate the venv with 3.12.
- Install `pip-system-certs` first; Cloudflare WARP breaks Python SSL on this laptop. Never use `verify=False`.
- The server binds `127.0.0.1:8765` only.
- Persona addresses the user as "sir" and never uses their name.
- Voice: Kokoro `bm_lewis`, speed `0.9`, lang `en-gb`. Model files go in `models/kokoro-v1.0.onnx` and `models/voices-v1.0.bin` (gitignored).
- STT: faster-whisper `large-v3-turbo`, `device="cuda"`, `compute_type="int8_float16"`; on failure use `device="cpu"`, `compute_type="int8"` and show a banner.
- Wake word `hey_jarvis`, threshold `0.5`; `end_silence_ms = 700`; listening hard cap 15 s; barge-in speech minimum 300 ms; `barge_in` default `"wake"`.
- History sent to the LLM: the last 20 messages.
- Budget: `monthly_inr = 300`; warn at ≥ 80 %; at ≥ 100 %, skip any route whose price is above 0.
- Secrets only through `keyring`, service name `"jarvis"`. `config.toml` holds key *names* only.
- Exact user-facing copy:
  - Empty transcript: `"Sorry sir, I didn't catch that."`
  - Stream broke after the first token: `"I lost the connection, sir."`
  - All routes failed: `"I can't reach any model right now, sir."`
  - Budget warning (spoken once a day plus a banner): `"Sir, we've used 80 percent of this month's budget."`
  - Mic missing (banner): `"Voice off, text only"`
- Run tests with: `.venv/Scripts/python -m pytest -q`
- Edge path: `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`
- One deliberate change from the spec: route prices live on each `[routes.*]` entry (`price_in`, `price_out`, ₹ per 1M tokens, default 0), not in a separate `[prices]` table.

## Review Focus

1. **Decimals and abbreviations in spoken text.** "The cutoff is 3.5 kHz." must stay one sentence; don't split on a `.` followed by a digit. Test in Task 3.
2. **Typing while it speaks.** A new `text_input` during a turn cancels the old turn (saved with `interrupted=1`) and starts the new one; replies never overlap. Test in Task 6.
3. **Mic that won't open at 16 kHz.** Many Windows mics only support 44.1 or 48 kHz. Open at the device default and resample to 16 kHz. Test of `resample_to_16k` in Task 5.
4. **First launch with no API keys.** The app starts, routes without keys are skipped, and the local route answers. Test in Task 2.
5. **Empty or whitespace text input.** Ignored: no turn and no DB row. Test in Task 6.

---

### Task 1: Project scaffold, config and database

**Files:**
- Create: `pyproject.toml`, `requirements.txt`, `config.toml`, `jarvis/__init__.py`, `jarvis/config.py`, `jarvis/db.py`, `tests/test_db.py`
- Modify: `.gitignore` (add `models/`)

**Interfaces:**
- Produces (`jarvis/config.py`):
  - `@dataclass Route: name: str; base_url: str; model: str; key: str; price_in: float = 0; price_out: float = 0`
  - `@dataclass Config: routes: dict[str, Route]; default_route: str; fallback: list[str]; monthly_inr: float; persona: str; barge_in: str; wake_threshold: float; end_silence_ms: int; ptt_key: str; voice: str; voice_speed: float; whisper_model: str; db_path: Path`
  - `load_config(path: Path = Path("config.toml")) -> Config`
  - `get_key(name: str) -> str | None`, `set_key(name: str, value: str) -> None` (keyring service `"jarvis"`)
- Produces (`jarvis/db.py`):
  - `connect(path: Path | str) -> sqlite3.Connection`: creates the parent dir and both tables from spec §7; `row_factory = sqlite3.Row`; `check_same_thread=False`
  - `add_message(conn, role: str, content: str, screen: str = "", route: str = "", interrupted: bool = False) -> int`
  - `recent_messages(conn, n: int = 20) -> list[dict]`: oldest first, keys `role, content, screen, route, interrupted`
  - `record_usage(conn, provider: str, model: str, tokens_in: int, tokens_out: int, cost_inr: float, now: datetime | None = None) -> None`
  - `month_spend_inr(conn, now: datetime | None = None) -> float`
  - `requests_today(conn, now: datetime | None = None) -> dict[str, int]`: provider → count
  - `budget_state(spent: float, cap: float) -> Literal["ok", "warn", "over"]`

- [ ] **Step 1: Create the venv and install deps**

`requirements.txt`: `pip-system-certs fastapi uvicorn[standard] openai faster-whisper openwakeword kokoro-onnx soundfile sounddevice numpy keyboard keyring pytest httpx`.
Run: `python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt`
Then: `.venv/Scripts/python -c "import fastapi, openai, faster_whisper, openwakeword, kokoro_onnx, sounddevice, keyboard, keyring; print('ok')"`
Expected: `ok`. If a cp313 wheel is missing, recreate the venv with `py -3.12` and record that in `pyproject.toml` `requires-python`.

- [ ] **Step 2: Write `config.toml`**

Use the routes from spec §6 (fast = Groq `llama-3.3-70b-versatile`, reasoning = Gemini `gemini-2.5-flash`, local = Ollama `qwen3:4b` with `key = ""`), `[routing] default/fallback`, `[budget] monthly_inr = 300`, `[voice] voice = "bm_lewis"`, `speed = 0.9`, `barge_in = "wake"`, `wake_threshold = 0.5`, `end_silence_ms = 700`, `ptt_key = "right ctrl"`, `whisper_model = "large-v3-turbo"`, `[persona] prompt = """…"""` (the spec §5 prompt verbatim), and `[data] db_path = "data/jarvis.db"`.

- [ ] **Step 3: Write the failing tests in `tests/test_db.py`**

```python
def test_recent_messages_oldest_first_and_limited(tmp_path):
    c = db.connect(tmp_path / "j.db")
    for i in range(25): db.add_message(c, "user", f"m{i}")
    r = db.recent_messages(c, 20)
    assert len(r) == 20 and r[0]["content"] == "m5" and r[-1]["content"] == "m24"

def test_interrupted_flag_roundtrip(tmp_path):
    c = db.connect(tmp_path / "j.db")
    db.add_message(c, "assistant", "Hello sir.", interrupted=True)
    assert db.recent_messages(c)[0]["interrupted"] == 1

def test_month_spend_ignores_last_month(tmp_path):
    c = db.connect(tmp_path / "j.db")
    c.execute("INSERT INTO usage(ts,provider,model,tokens_in,tokens_out,cost_inr) VALUES('2026-09-30T23:00:00','g','m',1,1,50)")
    db.record_usage(c, "g", "m", 1, 1, 20.0, now=datetime(2026, 10, 9, 12))
    assert db.month_spend_inr(c, datetime(2026, 10, 9)) == 20.0

def test_requests_today_per_provider(tmp_path):
    c = db.connect(tmp_path / "j.db")
    db.record_usage(c, "groq", "m", 1, 1, 0); db.record_usage(c, "groq", "m", 1, 1, 0)
    assert db.requests_today(c) == {"groq": 2}

def test_budget_state_thresholds():
    assert db.budget_state(239, 300) == "ok"
    assert db.budget_state(240, 300) == "warn"
    assert db.budget_state(300, 300) == "over"

def test_load_config_reads_routes():
    cfg = load_config(Path("config.toml"))
    assert cfg.default_route == "fast" and cfg.routes["local"].key == ""
    assert cfg.voice == "bm_lewis" and cfg.monthly_inr == 300
```
Timestamps are local `datetime.isoformat()` strings.

- [ ] **Step 4: Run and confirm the tests fail**: `.venv/Scripts/python -m pytest tests/test_db.py -q` → errors on import.
- [ ] **Step 5: Implement `config.py` and `db.py`** to the interfaces above. `load_config` uses `tomllib`.
- [ ] **Step 6: Run and confirm the tests pass**: `pytest tests/test_db.py -q` → 6 passed.
- [ ] **Step 7: Commit**: `git add -A && git commit -m "feat: scaffold, config and sqlite store"`

---

### Task 2: LLM routing with fallback, usage and budget cap

**Files:** Create `jarvis/llm.py`, `tests/test_llm.py`

**Interfaces:**
- Consumes: `Config`, `Route`, `get_key`, `db.record_usage`, `db.month_spend_inr`, `db.budget_state`
- Produces:
  - `class AllRoutesFailed(Exception)`, `class StreamBroken(Exception)`
  - `make_client(route: Route) -> openai.OpenAI`: `api_key=get_key(route.key)`, or `"ollama"` when `route.key == ""`; `max_retries=0`
  - `route_order(cfg: Config, first: str) -> list[str]`: `[first] + [r for r in cfg.fallback if r != first]`
  - `stream_reply(cfg, conn, messages: list[dict], route: str, client_factory=make_client) -> Iterator[tuple[str, str]]`: yields `("route", name)` once, when a route has produced its first token, then `("text", delta)` items
- Rules: skip a route whose `key` is non-empty but `get_key` returns None. Skip a route with `price_in` or `price_out` above 0 when `budget_state(...) == "over"`. Any `openai.APIError` raised before the first token → try the next route. Any exception after the first token → `StreamBroken`. If no route produces a token → `AllRoutesFailed`. Call `create(model=…, messages=…, stream=True, stream_options={"include_usage": True})`. Record one usage row per successful call, using the final chunk's `usage` or `len(text)//4` when it's absent; `cost_inr = (in*price_in + out*price_out)/1e6`; `provider = route.name`.

- [ ] **Step 1: Write the failing tests.** The fake client is `SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fn)))`. Chunks are `SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=t))], usage=None)`. Monkeypatch `jarvis.llm.get_key` to `lambda n: "k"`.

```python
def test_happy_path_yields_route_then_text(cfg, conn): ...
    assert list(stream_reply(cfg, conn, msgs, "fast", factory({"fast": ok("Hi", " sir.")}))) == \
        [("route", "fast"), ("text", "Hi"), ("text", " sir.")]

def test_429_falls_back_to_reasoning(cfg, conn):
    events = list(stream_reply(cfg, conn, msgs, "fast",
                  factory({"fast": raises(rate_limit_error()), "reasoning": ok("Hello.")})))
    assert events[0] == ("route", "reasoning")

def test_connection_error_falls_back(cfg, conn): ...   # openai.APIConnectionError → next route

def test_break_after_first_token_raises_streambroken(cfg, conn):
    with pytest.raises(StreamBroken):
        list(stream_reply(cfg, conn, msgs, "fast", factory({"fast": ok_then_raise("Hi")})))

def test_all_fail_raises(cfg, conn): ...                 # every route raises → AllRoutesFailed

def test_missing_key_routes_skipped(cfg, conn, monkeypatch):
    monkeypatch.setattr(llm, "get_key", lambda n: None)
    events = list(stream_reply(cfg, conn, msgs, "fast", factory({"local": ok("Hi.")})))
    assert events[0] == ("route", "local")

def test_paid_route_skipped_when_over_budget(cfg, conn):
    cfg.routes["fast"].price_in = 10; db.record_usage(conn, "x", "m", 0, 0, 300)
    assert list(stream_reply(cfg, conn, msgs, "fast", factory({"fast": ok("A"), "reasoning": ok("B")})))[0] == ("route", "reasoning")

def test_usage_row_written(cfg, conn): ...               # after happy path: requests_today(conn) == {"fast": 1}
```
`cfg` fixture: `load_config(Path("config.toml"))` with `db_path` replaced by `tmp_path`. `factory(map)` returns a `client_factory` that picks behaviour by `route.name` and raises `openai.APIConnectionError` for names it doesn't know.

- [ ] **Step 2: Run and confirm the tests fail.** `pytest tests/test_llm.py -q` → ImportError.
- [ ] **Step 3: Implement `llm.py`** to the interfaces and rules above.
- [ ] **Step 4: Run and confirm the tests pass.** 8 passed.
- [ ] **Step 5: Commit.** `git commit -am "feat: llm routing with fallback, usage and budget cap"` (add new files first)

---

### Task 3: Brain: splitter, route choice and turn

**Files:** Create `jarvis/brain.py`, `tests/test_brain.py`

**Interfaces:**
- Consumes: `llm.stream_reply`, `StreamBroken`, `AllRoutesFailed`, `db.add_message`, `db.recent_messages`, `db.month_spend_inr`, `db.budget_state`
- Produces:
  - `class Splitter:` `feed(delta: str) -> list[tuple[str, str]]` and `flush() -> list[tuple[str, str]]`. Events are `("speak", sentence)` or `("screen", text)`.
  - `pick_route(text: str, ui_route: str) -> str`: returns `"reasoning"` when `text.strip().lower()` starts with `"think carefully"`, otherwise `ui_route`
  - `Emit = Callable[[dict], Awaitable[None]]`, `Speak = Callable[[str], Awaitable[None]]` (returns once the sentence has finished playing)
  - `async def run_turn(cfg, conn, user_text: str, ui_route: str, emit: Emit, speak: Speak) -> None`
- Splitter algorithm: buffer text. Until a line equal to `---` (after strip) is complete, cut sentences at `[.?!]` followed by whitespace or end of a flushed buffer (never at `.` followed by a digit), or at `\n`. Strip each sentence and drop empties. Emit the first two as `speak`; every later sentence becomes `screen`. After `---`, everything is `screen`, passed through verbatim (newlines kept). Hold back any partial trailing line that could still turn out to be `---`.
- `run_turn` behaviour:
  1. Save the user message.
  2. Build `[{"role":"system","content":cfg.persona}] + [{"role","content"} of recent_messages(conn, 20)]`. The user message is already included because it was saved first.
  3. Run `stream_reply` in a worker thread, pumping into an `asyncio.Queue`.
  4. Emit `{"type":"route","route":…}` and `{"type":"state","state":"thinking"}`, then for each splitter event:
     - `speak`: put it in a sentence queue drained by a speaker sub-task that calls `await speak(s)`, appends `s` to `spoken`, and emits `{"type":"spoken_sentence","text":s}`. Emit `state: speaking` before the first sentence.
     - `screen`: emit `{"type":"screen_delta","text":…}`.
  4b. If the route that answered differs from the one requested, emit `{"type":"banner","text":f"{requested} unavailable, answered via {route}"}`.
  5. Await the speaker task, save the assistant message (`content=" ".join(spoken)`, `screen`, `route`), and emit `{"type":"turn_end"}`.
  6. On `CancelledError`: cancel the sub-tasks, save the assistant message with `" ".join(spoken)` and `interrupted=True`, emit `turn_end`, then re-raise.
  7. On `StreamBroken`: speak the "lost the connection" copy. On `AllRoutesFailed`: speak the "can't reach any model" copy and emit `{"type":"banner","text":str(e)}`.
  8. Afterwards, if `budget_state` is `"warn"` or `"over"` and the module-level `_warned_on` is not today: speak the budget copy, emit a banner, and set `_warned_on`.

- [ ] **Step 1: Write the failing tests**

```python
def run(sp, *chunks): return [e for c in chunks for e in sp.feed(c)] + sp.flush()

def test_two_spoken_then_screen():
    assert run(Splitter(), "Hello sir. It is 5 pm.\n---\n# Detail\nx") == \
        [("speak","Hello sir."),("speak","It is 5 pm."),("screen","# Detail\nx")]

def test_no_separator_third_sentence_goes_to_screen():
    ev = run(Splitter(), "One. Two. Three.")
    assert ev[:2] == [("speak","One."),("speak","Two.")] and ev[2] == ("screen","Three.")

def test_separator_split_across_chunks():
    assert ("screen","body") in run(Splitter(), "Hi sir.\n-", "--\nbody")
    assert all(t != "speak" or "-" not in s for t, s in run(Splitter(), "Hi sir.\n-", "--\nbody"))

def test_decimal_not_a_sentence_break():          # Review Focus 1
    assert run(Splitter(), "The cutoff is 3.5 kHz. Done.") == \
        [("speak","The cutoff is 3.5 kHz."),("speak","Done.")]

def test_pick_route():
    assert pick_route("Think carefully: BJT vs MOSFET", "fast") == "reasoning"
    assert pick_route("hello", "local") == "local"

async def test_interrupted_turn_saves_only_spoken(cfg, conn, monkeypatch):
    monkeypatch.setattr(brain, "stream_reply", lambda *a, **k: iter([("route","fast"),("text","First. Second.")]))
    gate = asyncio.Event(); said = []
    async def speak(s):
        said.append(s)
        if len(said) == 2: await gate.wait()       # hang on the second sentence
    t = asyncio.create_task(brain.run_turn(cfg, conn, "hi", "fast", noop_emit, speak))
    await asyncio.sleep(0.1); t.cancel()
    with pytest.raises(asyncio.CancelledError): await t
    last = db.recent_messages(conn)[-1]
    assert last["content"] == "First." and last["interrupted"] == 1

async def test_all_routes_failed_speaks_copy(cfg, conn, monkeypatch): ...
    # stream_reply raises AllRoutesFailed → speak called with "I can't reach any model right now, sir."
```
Add a `conftest.py` with a `pytest_pyfunc_call` hook that runs `async def` tests via `asyncio.run`, plus shared `cfg` and `conn` fixtures. Don't add pytest-asyncio.

- [ ] **Step 2: Run and confirm the tests fail.** `pytest tests/test_brain.py -q` → ImportError.
- [ ] **Step 3: Implement `brain.py`.**
- [ ] **Step 4: Run and confirm all tests pass.** `pytest -q` → all green.
- [ ] **Step 5: Commit.** `"feat: brain turn with spoken/screen split and cancellation"`

---

### Task 4: Voice state machine (pure)

**Files:** Create `jarvis/voice.py` (FSM part only), `tests/test_voice_states.py`

**Interfaces:**
- Produces:
  - `class State(str, Enum): IDLE="idle"; LISTENING="listening"; THINKING="thinking"; SPEAKING="speaking"`
  - `class VoiceFSM: __init__(self, barge_in: str)`, with `state: State` and `handle(event: str) -> list[str]` returning actions
- Transition table (anything not listed is ignored and returns `[]`):

| State | Event | → State | Actions |
|---|---|---|---|
| IDLE | `wake`, `hotkey_down` | LISTENING | `chime`, `start_buffer` |
| IDLE | `text_input` | THINKING | — |
| LISTENING | `vad_end`, `hotkey_up`, `timeout` | THINKING | `transcribe` |
| THINKING | `transcribed` | THINKING | `start_turn` |
| THINKING | `transcribed_empty` | SPEAKING | `say_didnt_catch` |
| THINKING | `speak_start` | SPEAKING | — |
| THINKING / SPEAKING | `turn_done` | IDLE | — |
| SPEAKING | `wake`, `hotkey_down` | LISTENING | `cancel_turn`, `chime`, `start_buffer` |
| SPEAKING | `speech_300ms` (only if `barge_in == "voice"`) | LISTENING | `cancel_turn`, `start_buffer` |
| any but IDLE | `cancel` | IDLE | `cancel_turn` |
| any | `text_input` (not IDLE) | THINKING | `cancel_turn` |

- [ ] **Step 1: Write the failing tests.** One test per row group, for example:

```python
def test_wake_barge_in_during_speech():
    f = VoiceFSM("wake"); f.state = State.SPEAKING
    assert f.handle("wake") == ["cancel_turn", "chime", "start_buffer"] and f.state is State.LISTENING

def test_voice_barge_in_ignored_in_wake_mode():
    f = VoiceFSM("wake"); f.state = State.SPEAKING
    assert f.handle("speech_300ms") == [] and f.state is State.SPEAKING

def test_voice_barge_in_in_voice_mode():
    f = VoiceFSM("voice"); f.state = State.SPEAKING
    assert f.handle("speech_300ms") == ["cancel_turn", "start_buffer"]
```
Also cover: idle→listening on `hotkey_down`; listening→thinking on `vad_end` with `["transcribe"]`; `transcribed_empty` → `["say_didnt_catch"]`; `cancel` from THINKING; `turn_done` → IDLE; `wake` while THINKING is ignored.

- [ ] **Step 2: Run and confirm the tests fail.**
- [ ] **Step 3: Implement `VoiceFSM`** as a dict keyed by `(state, event)`, plus the two "any state" rules.
- [ ] **Step 4: Run and confirm the tests pass.**
- [ ] **Step 5: Commit.** `"feat: voice state machine"`

---

### Task 5: Voice hardware loop (mic, wake word, VAD, STT, TTS)

**Files:** Modify `jarvis/voice.py` (add the hardware part). Create `tests/test_resample.py`.

**Interfaces:**
- Consumes: `VoiceFSM`, `Config`
- Produces:
  - `resample_to_16k(x: np.ndarray, sr: int) -> np.ndarray` (float32, `np.interp`; returns `x` unchanged when `sr == 16000`)
  - `class VoiceLoop: __init__(self, cfg: Config, on_event: Callable[[str, dict], None])`. `on_event` is called from the voice thread with `("state", {"state": …})`, `("transcript", {"text": …})`, `("cancel_turn", {})` or `("banner", {"text": …})`.
    - `start() -> None`: loads models, opens the mic, starts the daemon thread. If the mic fails, sends `banner "Voice off, text only"` and retries every 10 s.
    - `async speak(text: str) -> None`: Kokoro synth in a thread, then playback through `sounddevice`. Returns when playback ends. Cancellation calls `sd.stop()`.
    - `event(name: str) -> None`: thread-safe entry for `hotkey_down`, `hotkey_up`, `text_input`, `speak_start`, `turn_done` and `cancel`. It feeds the FSM and executes the actions. `transcribe` runs Whisper, then fires `transcribed` with `on_event("transcript", …)`, or fires `transcribed_empty`. `say_didnt_catch` speaks the empty-transcript copy, then fires `turn_done`. `cancel_turn` calls `on_event("cancel_turn", {})`.
    - LISTENING ends with `timeout` after 15 s. `speech_300ms` fires during SPEAKING when the VAD sees 300 ms or more of continuous speech.
    - If Kokoro or playback raises inside `speak`, send `banner "Voice output failed, showing text"` and return normally, so the turn completes as text.
    - `available: bool`
- Hardware details:
  - Mic `InputStream` at the device default rate, mono, 80 ms blocks, then `resample_to_16k` into 1280-sample int16 frames.
  - `openwakeword.Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")`. Call `openwakeword.utils.download_models(["hey_jarvis"])` once.
  - VAD comes from `openwakeword.vad.VAD()` (bundled Silero). Speech when prob > 0.5. If that import path differs in the installed version, use the Silero ONNX model from `faster_whisper.vad` instead, without adding a dependency.
  - Whisper is loaded once and called with `language="en"`, `beam_size=1`.
  - Kokoro is `Kokoro("models/kokoro-v1.0.onnx", "models/voices-v1.0.bin")`, using `cfg.voice` and `cfg.voice_speed` with `lang="en-gb"`.
  - The chime is a 120 ms 880 Hz sine generated with numpy.
  - Wake-word scores are only checked in IDLE and SPEAKING. Reset the wake model after each hit so a single "Hey Jarvis" doesn't fire twice.

- [ ] **Step 1: Get the model files.** Copy `kokoro-v1.0.onnx` and `voices-v1.0.bin` from the scratchpad samples folder (`…\scratchpad\voices\`), or download them from `https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/`, into `models/`.
- [ ] **Step 2: Install the CUDA runtime.** `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12==9.*`. Then, at the top of `voice.py`, call `os.add_dll_directory` for each `nvidia/*/bin` under site-packages, so that ctranslate2 finds them on Windows.
- [ ] **Step 3: Write the failing test** (Review Focus 3)

```python
def test_resample_48k_to_16k_length_and_passthrough():
    x = np.sin(np.linspace(0, 100, 4800)).astype(np.float32)
    assert len(resample_to_16k(x, 48000)) == 1600
    assert resample_to_16k(x, 16000) is x
```
- [ ] **Step 4: Run it and confirm it fails, implement the hardware part, run it and confirm it passes.**
- [ ] **Step 5: Add a self-test entry point** with `if __name__ == "__main__"` in `voice.py`. `--selftest` speaks "Good evening, sir. Systems are online.", records 4 s after a chime, prints the transcript, then prints Whisper and Kokoro timings plus a CUDA yes/no.
Run: `.venv/Scripts/python -m jarvis.voice --selftest`
Expected: you hear Lewis, the transcript printed matches what you said, and Whisper takes under 1 s for 4 s of audio on CUDA.
- [ ] **Step 6: Commit.** `"feat: voice loop hardware (wake, vad, whisper, kokoro)"`

---

### Task 6: Server and UI

**Files:** Create `jarvis/app.py`, `jarvis/ui/index.html`, `jarvis/ui/marked.min.js`, `jarvis/ui/purify.min.js`, `tests/test_app.py`

**Interfaces:**
- Consumes: `brain.run_turn`, `brain.pick_route`, `db.recent_messages`, `db.month_spend_inr`, `db.requests_today`, `VoiceLoop` (optional)
- Produces: `create_app(cfg, conn, voice: VoiceLoop | None = None, turn_runner=brain.run_turn) -> FastAPI`, plus app state `app.state.hub` with:
  - `async broadcast(msg: dict)`: sends to all sockets
  - `start_turn(text: str) -> None`: strips the text and ignores empty input. Cancels and awaits any current turn, then creates a new task `turn_runner(cfg, conn, text, pick_route(text, route), broadcast, speak)`. `speak` is `voice.speak` when voice is available, otherwise a no-op. It emits `{"type":"user_text","text":…}` first, and calls `voice.event("text_input")` when voice is available.
  - `cancel() -> None`
  - `route: str` (starts at `cfg.default_route`)
  - `from_thread(name, payload)`: the bridge for `VoiceLoop.on_event`, using `loop.call_soon_threadsafe`. `transcript` → `start_turn`; `cancel_turn` → `cancel`; `state` and `banner` → broadcast.
- WebSocket `/ws`:
  - On connect, send `{"type":"history","messages":recent_messages(conn,20)}` and a `usage` event `{"type":"usage","spent_inr":…,"cap_inr":…,"requests_today":{…}}`.
  - Messages in: `{"type":"text_input","text"}`, `{"type":"cancel"}`, `{"type":"set_route","route"}`.
  - After each `turn_end`, broadcast fresh `usage`.
- UI (`index.html`, one file, vanilla JS):
  - An orb whose CSS class is the state; clicking it sends `cancel`.
  - A transcript of user and spoken lines, and a detail pane re-rendered from accumulated `screen_delta` through `DOMPurify.sanitize(marked.parse(…))`.
  - A text box (Enter to send), a route `<select>` (fast / reasoning / local), a usage meter `₹{spent} / ₹{cap} · {provider} {n} req today`, and a banner strip.
  - Vendor `marked@12.0.2` and `dompurify@3.1.6` from cdnjs as local files; the page loads nothing from the network.

- [ ] **Step 1: Write the failing tests** with `fastapi.testclient.TestClient` and a fake `turn_runner`

```python
def test_text_input_runs_turn_and_streams_events(app_with_fake):
    with TestClient(app).websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "history"; ws.receive_json()  # usage
        ws.send_json({"type": "text_input", "text": "hello"})
        types = [ws.receive_json()["type"] for _ in range(3)]
        assert types == ["user_text", "spoken_sentence", "turn_end"]

def test_empty_text_ignored(...):                      # Review Focus 5
    # send "   " then a real "hi": the first event received is user_text "hi"; the fake runner was called once

def test_new_text_cancels_running_turn(...):           # Review Focus 2
    # fake runner #1 awaits forever (records CancelledError); send "a" then "b"
    # assert runner 1 saw CancelledError and runner 2 ran with "b"

def test_set_route_used_for_next_turn(...):
    # send set_route local then text_input → fake runner received ui_route "local"
```
- [ ] **Step 2: Run and confirm the tests fail. Implement. Run and confirm they pass.**
- [ ] **Step 3: Check the UI by hand.** Run `.venv/Scripts/python -c "from jarvis.app import create_app; …"` (or wait for Task 7). Open `http://127.0.0.1:8765`, type "hello", and get a reply via a real route.
- [ ] **Step 4: Commit.** `"feat: websocket server and desktop UI"`

---

### Task 7: Entry point, hotkey, keys CLI and acceptance demo

**Files:** Create `jarvis/__main__.py`, `README.md` (setup steps and demo script). Modify `.gitignore` if needed.

**Interfaces:**
- Consumes: everything above
- Produces:
  - `python -m jarvis keys set <name>` reads the value with `getpass` and calls `set_key`. `python -m jarvis keys list` shows which of `groq` and `gemini` are set (never the values).
  - `python -m jarvis` does the following:
    1. `load_config`, then `db.connect`.
    2. Builds `VoiceLoop(cfg, hub.from_thread)` inside a FastAPI startup hook, so `hub` and the loop exist. If its init fails, `voice=None` and a banner.
    3. Prints a line for each missing key, naming the `keys set` command.
    4. Sets the push-to-talk hotkey with `keyboard.on_press_key(cfg.ptt_key, …)` and `on_release_key`, firing `hotkey_down` and `hotkey_up` with key-repeat debounced.
    5. Starts `uvicorn.run(app, host="127.0.0.1", port=8765)`.
    6. Launches `[EDGE, "--app=http://127.0.0.1:8765"]` via `subprocess.Popen` once the server is up.
  - The VoiceLoop `on_event` bridge also pushes `speak_start` and `turn_done` back into the FSM. `run_turn`'s `state: speaking` and `turn_end` events are mirrored to `voice.event(...)` inside `hub.broadcast`.

- [ ] **Step 1: Implement `__main__.py`.**
- [ ] **Step 2: Do the one-time setup.** `ollama pull qwen3:4b`, then `python -m jarvis keys set groq` and `python -m jarvis keys set gemini`. Free keys come from console.groq.com and aistudio.google.com.
- [ ] **Step 3: Run the full test suite.** `.venv/Scripts/python -m pytest -q` → all pass.
- [ ] **Step 4: Run the acceptance demo,** spec §10 steps 1–8, on this laptop. Record which steps passed, plus the measured time from the end of speech to first audio (target < 1.5 s on `fast`).
- [ ] **Step 5: Write `README.md`.** Cover setup (Global Constraints and Tasks 1, 2, 5 and 7 setup steps in order), the 8-step demo script, optional autostart (a shortcut to `pythonw -m jarvis` in `shell:startup`), and running cost: ₹0 a month.
- [ ] **Step 6: Commit.** `"feat: entry point, hotkey, keys CLI; phase 1 complete"`
