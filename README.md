# JARVIS (Phase 1: voice loop)

A local voice and text assistant. Say "Hey Jarvis", hold **Right Ctrl**, or type.
It answers in a short spoken line (the Kokoro "Lewis" voice) and puts longer
detail on screen. Everything runs on this laptop except the LLM calls to Groq
and Gemini (both free tiers).

Design: `docs/superpowers/specs/2026-10-09-phase1-voice-loop-design.md`

## Setup (one time)

Windows 11 with an NVIDIA GPU, Python 3.13.

```powershell
cd ~\jarvis
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
# kokoro-onnx pulls in the CPU onnxruntime, which shadows the GPU build; swap it:
.venv\Scripts\python -m pip uninstall -y onnxruntime
.venv\Scripts\python -m pip install --force-reinstall --no-deps onnxruntime-gpu==1.23.2
```

- **Don't install `pip-system-certs`** in this venv. `openai` and `huggingface_hub`
  already use the Windows certificate store (so Cloudflare WARP works), and
  pip-system-certs makes them crash with `RecursionError`.
- **Voice model:** put `kokoro-v1.0.onnx` and `voices-v1.0.bin` in `models\`
  (from https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0).
  Whisper and the wake word download themselves on first run, about 1.6 GB.
- **Local model:** install Ollama (`winget install Ollama.Ollama`), then
  `ollama pull qwen3:4b-instruct`.
- **API keys** (free). Get them from https://console.groq.com/keys and https://aistudio.google.com/apikey:
  ```powershell
  .venv\Scripts\python -m jarvis keys set groq
  .venv\Scripts\python -m jarvis keys set gemini
  .venv\Scripts\python -m jarvis keys list     # shows set/missing, never the value
  ```
  Keys go to Windows Credential Manager, never into files.
- **Optional:** start at login. Win+R → `shell:startup`, then create a shortcut to
  `C:\Users\HP VICTUS\jarvis\.venv\Scripts\pythonw.exe -m jarvis`
  with "Start in" set to `C:\Users\HP VICTUS\jarvis`.

Check the voice hardware on its own: `.venv\Scripts\python -m jarvis.voice --selftest`

## Run

```powershell
.venv\Scripts\python -m jarvis
```

This opens an Edge app window at http://127.0.0.1:8765. Startup takes about
20 s while the voice models load. Settings live in `config.toml`: models,
voice, hotkey, barge-in mode and budget.

- **Orb colours:** grey = idle, blue = listening, amber = thinking,
  green = speaking. Click the orb to stop.
- **Barge-in:** on speakers, say "Hey Jarvis" or press Right Ctrl while it
  talks. If you wear headphones, set `barge_in = "voice"` in `config.toml`
  to interrupt just by speaking.
- **Routes:** `fast` (Groq) is the default. Start a request with "think
  carefully…" to use `reasoning` (Gemini). `local` (Ollama) runs fully
  offline. If a route fails, the next one in the chain answers and a banner
  tells you.

## Demo script (acceptance test)

1. Run `python -m jarvis`. The window opens, the orb is grey, and the meter
   shows ₹0.
2. Say "Hey Jarvis" (you hear a chime), then "good evening". You get a short
   spoken greeting addressed to "sir".
3. Say "Hey Jarvis, explain the Nyquist sampling theorem." It speaks one or
   two sentences and puts the full explanation in the right-hand pane.
4. While it's speaking, say "Hey Jarvis" (or press Right Ctrl). It stops
   almost at once and listens.
5. Hold Right Ctrl, ask "what did I just ask you about?", then release. It
   answers from the conversation history.
6. Type "think carefully: compare BJT and MOSFET as switches". It uses the
   reasoning route, shown in the status line.
7. Turn off Wi-Fi and ask something. It answers via `local`, with a banner.
8. Close and restart the app. The transcript reloads.

## Phase 2a: laptop control, memory and sub-agents

There's nothing new to install. On start, JARVIS reads your Start-menu apps and
builds a filename index of your home folder in the background. That takes about
15 seconds and is refreshed every 30 minutes.

- **Allowed folders:** `[files]` in `config.toml` (default: home, minus
  `.venv`, `node_modules`, `.git`, `AppData` and similar). File access is
  read-only: JARVIS can only find files by name and open them in their
  default app. It never opens programs or scripts as files.
- **Scripts:** put `.py`, `.ps1` or `.bat` files in `jarvis/scripts/`. Only
  these can run, and JARVIS always asks first.
- **Confirmations:** closing a window, running a script, and anything a
  sub-agent wants to do all need your approval. Say "yes" or "no" within 8
  seconds (no wake word needed), or click **Confirm** or **Cancel**. Silence
  counts as no.
- **Action log and undo:** open **Actions** under the header. Each action has
  an **Undo** button where undo is possible, or you can say "undo that".
- **Sub-agents:** `[agents.*]` in `config.toml`. `thinker` uses Gemini for
  hard reasoning. `private` runs only on the local model and can search your
  files; it never falls back to the cloud.

### Phase 2a demo

1. "Hey Jarvis, open Notepad." Notepad opens, and Actions shows ✓ with Undo.
2. "Snap Notepad to the left." Then: "Undo that." It returns to where it was.
3. "Find my files with motor in the name." The paths appear on screen.
4. "Remember that my lab is on Thursdays." Restart JARVIS, then ask "When is
   my lab?"
5. "Close Notepad." It asks "Close Notepad, sir?"; say "yes" and it closes.
6. Put `hello.py` (`print("hello")`) in `jarvis/scripts/`. Say "Run the hello
   script" and answer "no": it is cancelled. Ask again and click Confirm: the
   output appears on screen.
7. "Ask the thinker to plan my week around two exams and a lab report." The
   status shows "Consulting thinker…", then you get the answer.
8. "Ask the private agent which PDFs I have about motors." It asks before
   searching; confirm, and it answers using only the local model.

## Cost

₹0 a month. Groq and Gemini are on free tiers, and Whisper, Kokoro and Ollama
run locally. The meter counts requests per provider. Rupees only appear if a
paid route is added (set `price_in` and `price_out` on that route in `config.toml`).

## Tests

`.venv\Scripts\python -m pytest -q`. These need no mic, GPU or network.
