# JARVIS

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

- **Island and WhatsApp (Node):** `npm install --prefix desktop` and
  `npm install --prefix desktop/wa`. Behind Cloudflare WARP set
  `NODE_OPTIONS=--use-system-ca` first. Without them JARVIS falls back to the Edge
  window and WhatsApp goes through n8n (if set up).
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

This shows the **island** at the top of your screen. Startup takes about
20 s while the voice models load. Settings live in `config.toml`: models,
voice, hotkey, barge-in mode and budget.

### The island

A small black pill that floats above every app: the orb on the left, the mic on
the right. It never takes your keyboard focus, and clicks around it go to the
app underneath.

- **What it shows:**
  - **Idle:** a ticker of what's next (class, weather, deadline, a reminder).
  - **Listening:** the orb turns blue and a level meter moves beside the mic,
    then your words appear.
  - **Thinking:** the orb turns amber, "Thinking" shimmers, and a light circles
    the island's edge until the answer starts.
  - **Speaking:** JARVIS's words, with a colour ring around the edge.
- **Hover** to peek at the last reply. **Hold the mic** to talk (same as Right
  Ctrl). **Click** the island to open the panel; Esc or the arrow closes it.
- **Questions** (WhatsApp sends, file edits, devices) grow the island into a
  card with Cancel and Send/Allow. Saying "yes" or "no" works too.
- **Running jobs** (a Claude Code task, a WhatsApp send, a reminder due within
  10 minutes) split off as a small bubble beside the island. Reminders and
  WhatsApp replies drop out of it as a card.
- **Fullscreen** games, videos and presentations: the island tucks away. "Hey
  Jarvis", a question or a reminder brings it back.
- **The panel:** glance cards (weather, next class, due soon), then three tabs:
  - **Chat:** your recent chats as chips, and the conversation laid out like
    WhatsApp. Voice turns have a mic mark.
  - **Artifacts:** what JARVIS made for you (files, saved answers, Claude Code
    commits). Click to open. Say "save this" to keep an answer as one.
  - **Vault:** what you told it to keep. Say "keep this…", or drag a file or
    text onto the island. Files are copied in (your original stays put);
    removing one sends it to the JARVIS trash, and "undo that" brings it back.
- **Full view** (the button in the panel header, or the tray icon) opens the
  big window described below.
- **Tray icon:** Full view, and Quit island (JARVIS keeps running).

### WhatsApp from your own number

One time: run `.venv\Scripts\python -m jarvis whatsapp link` and scan the QR code
with WhatsApp → Linked devices (or use "Link WhatsApp" at the bottom of the
Vault tab). The session stays in `data\wa_auth` on this laptop.
If the helper isn't installed, that row says so: run `npm install --prefix desktop/wa`.

- "WhatsApp Rahul that I'm running late." JARVIS finds the contact, then shows
  the name, number and the whole message and asks first. Nothing is sent
  without your yes.
- Two contacts match? It asks which one. None? It asks for the number.
- At most 20 messages an hour. Never from the phone or WhatsApp channel.
- It only says "sent" once WhatsApp's servers confirm it.
- Replies show as a card, only in chats JARVIS messaged in the last day. It
  doesn't read or keep anything else.
- This uses WhatsApp Web's protocol through an unofficial library, so there's a
  small risk WhatsApp limits the number.

### Full view

- **The orb:** one multicolour glow ring. Its motion shows the state: a slow
  turn when idle, brighter when listening, a fast spin when thinking, and it
  swells with the voice when speaking. It dims when offline. The status line
  in the header says the same in words. Click the orb to stop.
- **Input bar:** type, or mute the mic. The model picker (Fast, Reasoning,
  Local) chooses the route. The **+** menu adds files or photos (up to 10 MB
  each), takes a screenshot (JARVIS's own window left out), and turns on
  **Extended thinking** (the Reasoning route).
- **Side panel** (the button at the right of the input bar): **Chat** is the
  transcript, **Details** holds longer answers, tables and links, **History**
  lists past chats and starts a **New chat**, and **Neural** lights up the
  parts of JARVIS in use. **Show details** under a reply opens it too.
- **Barge-in:** on speakers, say "Hey Jarvis" or press Right Ctrl while it
  talks. If you wear headphones, set `barge_in = "voice"` in `config.toml`
  to interrupt just by speaking.
- **Routes:** `fast` (Groq) is the default. Start a request with "think
  carefully…" to use `reasoning` (Gemini). `local` (Ollama) runs fully
  offline. If a route fails, the next one in the chain answers and a banner
  tells you.

## Demo script (acceptance test)

1. Run `python -m jarvis`. The window opens and the status says "Ready".
2. Say "Hey Jarvis" (you hear a chime), then "good evening". You get a short
   spoken greeting addressed to "sir".
3. Say "Hey Jarvis, explain the Nyquist sampling theorem." It speaks one or
   two sentences; **Show details** opens the full explanation in the side panel.
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
  seconds (no wake word needed), or click **Allow** or **Cancel** (Escape cancels). Silence
  counts as no.
- **Undo:** say "undo that" to reverse the last action that can be undone.
  There is no undo button in the window.
- **Sub-agents:** `[agents.*]` in `config.toml`. `thinker` uses Gemini for
  hard reasoning. `private` runs only on the local model and can search your
  files; it never falls back to the cloud.

### Phase 2a demo

1. "Hey Jarvis, open Notepad." Notepad opens.
2. "Snap Notepad to the left." Then: "Undo that." It returns to where it was.
3. "Find my files with motor in the name." The paths appear on screen.
4. "Remember that my lab is on Thursdays." Restart JARVIS, then ask "When is
   my lab?"
5. "Close Notepad." It asks "Close Notepad, sir?"; say "yes" and it closes.
6. Put `hello.py` (`print("hello")`) in `jarvis/scripts/`. Say "Run the hello
   script" and answer "no": it is cancelled. Ask again and click Allow: the
   output appears on screen.
7. "Ask the thinker to plan my week around two exams and a lab report." The
   status shows "Consulting thinker…", then you get the answer.
8. "Ask the private agent which PDFs I have about motors." It asks before
   searching; confirm, and it answers using only the local model.

## Phase 2b: your files' contents, reversible changes, and the web

One-time setup:

```
.venv\Scripts\python -m pip install -r requirements.txt
ollama pull nomic-embed-text
ollama create jarvis-local -f Modelfile
```

- **Asking about your files:** JARVIS reads the contents of your notes, PDFs,
  slides, Word documents, notebooks and code in your home folder, all indexed
  locally in the background. The first pass takes a few minutes; after that only
  changed files are re-read.
  - Tool and build clutter is skipped (`content_exclude` in `config.toml`); those
    files can still be found by name.
  - **Privacy (`content_private` in `config.toml`):** currently `false`, so
    passages JARVIS reads from your files go to the cloud model answering
    (Groq or Gemini). That gives smarter answers, but your file text leaves the
    laptop.
    - Set it to `true` and, once JARVIS reads inside a file, the rest of that
      answer comes from the local model only.
    - Private mode is weaker: the small local model once claimed an edit it
      never made.
- **Changing files:** JARVIS can create files and folders, overwrite or edit text
  files, and move, rename or delete anything in your home folder.
  - **Nothing is permanent.** A deleted file, or the old version of an edited
    one, goes to `data\trash`, and "undo that" puts it back. Only you empty the trash.
  - Overwrite, edit, move and delete always ask first.
  - JARVIS never changes its own folder, and never writes programs or scripts
    (`.exe`, `.bat`, `.lnk`, …).
- **Web:** web search (DuckDuckGo, no key needed) and page reading, including
  online PDFs. Answers list their sources on screen. The `researcher` agent does
  multi-page research.
- **Prompt-injection guard:** web pages and files can contain instructions. After
  JARVIS reads one, any action with a side effect needs your confirmation for
  the rest of that turn.

### Coding goes to Claude Code

JARVIS never writes code itself; its own file tools only write notes and
documents. Anything involving code goes straight to Claude Code (`claude -p`) in
the project you name, for example "ask Claude to add a CSV export to
motor-fault-detection".

- **No question first:** JARVIS doesn't ask before sending the request. It
  does ask if, earlier in the same request, it read a web page or file that
  could have planted the instruction.
- **Projects:** the project must be a git repository in your home folder.
  JARVIS finds it by name.
- **Permissions and rules:** Claude Code runs with full permissions inside
  your standing rules:
  - no payments
  - nothing permanent (no history rewriting, nothing deleted outside git)
  - nothing outward-facing (no pushing, publishing or messages)
  - no secrets in code

  It runs the tests, commits, and JARVIS reads you its summary.
- **Undo:** "undo that" reverts the commit with `git revert`, so the history
  keeps both.
- **Usage:** this uses your Claude subscription's limits. A task is stopped
  after 20 minutes.

### Phase 2b demo

1. "What do my notes say about Thevenin's theorem?" The answer is built from your
   files, with the file and page shown on screen.
2. "Summarise BEEE unit 5." It finds and reads the PDF and answers locally.
3. "Create a file called todo.md on my Desktop with three study tasks." It's
   created.
4. "In todo.md, change three to four." It asks "Edit todo.md, sir?"; say yes,
   then "undo that" restores the old version.
5. "Delete todo.md." It asks, then moves the file to `data\trash`; "undo that"
   brings it back.
6. "Search the web for the ESP32-S3 datasheet." You get an answer with Sources.
7. "Ask the researcher to compare buck and boost converter efficiency." You get a
   researched answer with links.

## Phase 3: daily briefing, plan, reminders, accounts

- **Timetable, tasks, reminders:** tell JARVIS. For example:
  - "Add Physics on Monday 9 to 9:50 in TP-201."
  - "Lab report due Thursday."
  - "Remind me at 6 to call mom."

  Reminders are spoken and shown on time. If JARVIS was off at that moment, they
  play once as "late" when it next starts. Everything is undoable.
- **Briefing:** say "brief me", or let the morning briefing run on its own. It
  runs once a day after `[briefing] time` (07:30) while JARVIS is running.
  - It covers the weather at SRM, today's classes, deadlines, today's reminders,
    your calendar, important unread email, and GitHub.
  - It ends with a proposed plan for the day. Change it by voice ("move the gym
    to 7"), and JARVIS keeps it as today's plan.
- **GitHub:** works through your existing `gh` login. Ask "what's on GitHub?"
- **Google (Gmail, Calendar, Drive):** read email, save drafts (never sent; you
  send them), see and add calendar events, search Drive. Saving drafts and
  adding events ask first and can be undone.

### Connecting Google (one time, about 5 minutes)

1. At https://console.cloud.google.com, create a project. Then enable the
   **Gmail API**, **Google Calendar API** and **Google Drive API**.
2. Under **OAuth consent screen**, choose External, and add yourself as a
   **test user**.
3. Under **Credentials**, choose Create credentials → OAuth client ID →
   **Desktop app**. Download the JSON file and save it as
   `jarvis\data\google_client.json`.
4. Run `.venv\Scripts\python -m jarvis accounts connect google` and allow
   access in the browser.
5. Run `.venv\Scripts\python -m jarvis accounts list` to check that it says
   "google: connected".

## Phase 4: the away channel (phone and WhatsApp)

The same JARVIS, with the same memory, plan and tools, answers on the phone
(Vapi + Twilio) and on WhatsApp (your n8n workflow).

- **Safety when you're away:** nothing can be approved from a phone or WhatsApp.
  Anything with a side effect gets "that needs you at the desk", and Claude
  Code isn't available remotely. Reading things works: the briefing, your plan,
  your notes and the web.
- **Locked down:** through a tunnel, the desk UI is unreachable. Only the two
  remote endpoints answer, and only with your token. They're also limited to
  30 requests a minute.
- **Outbound:**
  - "Call me about it": a call to your own number, no question asked.
  - "Call +91… to …": reads the number and the purpose back first.
  - "WhatsApp … that …": asks first. Nothing is ever sent unasked.
- **Call-me reminders:** "remind me at 9 to leave, and call me". When it's due,
  JARVIS speaks it and phones your own number (never asks). A reminder that fires
  late because JARVIS was off doesn't call. Without `[remote]` set up, the reminder
  still fires and says the call couldn't be placed.
- **Call costs:** every outbound call counts on the usage meter. Once Vapi reports
  it ended, its length and cost are added to the month's spend, converted at
  `[remote] usd_inr` (default 88).

### Setting up the away channel

1. Run `.venv\Scripts\python -m jarvis remote token` and keep the token it
   prints. Running it again replaces the token, and the old one stops working.
2. Run `winget install Cloudflare.cloudflared`, then
   `.venv\Scripts\python -m jarvis remote tunnel`. Copy the
   `https://....trycloudflare.com` address. It changes each time the tunnel
   starts; ngrok's free fixed domain is an alternative.
3. **Vapi** (dashboard):
   - Create an assistant with Model → **Custom LLM**, URL `<address>/v1`, and
     the token as its key.
   - Attach your Twilio number. Then put the ids into `[remote]` in
     `config.toml` (`vapi_phone_id`, `vapi_assistant_id`, `my_number`).
   - Run `.venv\Scripts\python -m jarvis keys set vapi_key` and paste your
     Vapi **private** key.
   - For calling other people, create a second, normal Vapi assistant and put
     its id in `vapi_outbound_assistant_id`.
   - Twilio trial accounts can only call verified numbers.
4. **n8n:**
   - In your WhatsApp workflow, add an HTTP Request node:
     - POST to `<address>/api/message`, with the header
       `Authorization: Bearer <token>`.
     - Use the body fields "text" (the message) and "from" (the sender's
       number). Set them as fields, or as JSON built with
       `JSON.stringify(...)`, so a quote in a message can't break it.
     - Send `reply` back to the sender.
   - For JARVIS to send WhatsApp messages, add a webhook workflow that takes
     `{"to", "text"}`. Put its URL in `whatsapp_webhook`; if it checks a token,
     store it with `keys set n8n_token`.

**Only you get in:** the token proves a request came from Vapi or n8n, not
who's talking. So JARVIS only answers your own number: `my_number`, plus any
`allowed_numbers = ["+91…"]` in `[remote]`.
- WhatsApp messages from anyone else get no turn at all.
- Callers from other numbers hear "I can't verify who's calling". This also
  happens if Vapi doesn't send the caller's number.
- Caller ID can be spoofed, so nothing that changes anything ever works
  remotely.
- `[remote] allow = ["add_task"]` lets specific harmless tools through if you
  want them.

For the outbound assistant (calling other people), write its Vapi prompt
around `{{purpose}}`, which JARVIS fills in for each call.

## Phase 5: hardware (serial and MQTT)

- **Devices are config:** each `[[devices]]` entry in `config.toml` has a
  name, a transport (`serial` or `mqtt`) and the exact commands JARVIS may
  send. Adding a device is a config entry, never code, and JARVIS never sends
  anything that isn't listed.
- **Safety:**
  - Commands with `confirm = true` ask first ("Start the motor rig, sir?").
  - `stop` and `off` never ask. They even work from the phone, so you can
    always stop something.
  - After JARVIS has read an email, web page or file, everything except
    stop/off asks first.
  - From the phone or WhatsApp, only stop/off work.
  - Device names must match: "desk fan" never runs the "desk heater". You get
    "did you mean …?" instead.
  - Numbers are range-checked (`min`/`max`) before you're asked.
  - `undo = "stop"` lets "undo that" send the safe opposite.
- **Motor rig (from motor-fault-detection):** already set up. Plug in the
  ESP32 and say "start the motor rig", "set the motor rig speed to 400" or
  "stop the motor rig".
  - The port is found automatically, and opening it doesn't reset the ESP32.
    If several devices use `port = "auto"`, give each a `match = "CP210"`
    (part of its USB description).
  - The rig's firmware stops the motor about 2 seconds after its last `PING`.
    Long runs need the motor-fault dashboard, which sends pings; JARVIS sends
    single commands.
  - If the motor-fault dashboard is using the port, JARVIS says the port is
    busy. Stop the motor from the dashboard then.
- **MQTT:** needs a broker (for example, Mosquitto). See the commented
  example in `config.toml`.
  - If the broker needs a login, set `username` on the device. Store its
    password with `keys set mqtt_<device_name>`, using underscores for spaces,
    e.g. `mqtt_desk_lamp`.
  - If the broker doesn't confirm a message, JARVIS says so instead of
    "sent".

## Cost

₹0 a month. Groq and Gemini are on free tiers, and Whisper, Kokoro and Ollama
run locally. Requests are counted per provider in the database; the window
shows no meter. If a paid route is added (set `price_in` and `price_out` on it
in `config.toml`), its spend is tracked and the route is skipped once
`[budget] monthly_inr` is reached.

## Tests

`.venv\Scripts\python -m pytest -q`. These need no mic, GPU or network.
