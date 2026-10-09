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

## Cost

₹0 a month. Groq and Gemini are on free tiers, and Whisper, Kokoro and Ollama
run locally. The meter counts requests per provider. Rupees only appear if a
paid route is added (set `price_in` and `price_out` on that route in `config.toml`).

## Tests

`.venv\Scripts\python -m pytest -q`. These need no mic, GPU or network.
