# JARVIS Island UI: design

Status: design approved in conversation with the user (2026-10-23), section by section, with an
interactive mockup. This spec waits for the user's review before the implementation plan.

Mockup (open in a browser): `docs/superpowers/specs/assets/2026-10-23-island-mockup.html`.
It is the visual source of truth for sizes, colours and motion; this document is the source of
truth for behaviour.

## 1. Goal

Replace the everyday JARVIS window with a floating **island**: a small black pill that sits
above every app (not a notch; it floats 10 px below the top edge, centred on the main screen).
It holds the orb on the left, a small gap, and a mic button on the right. Clicking it expands it
into a compact translucent panel with the recent chats, the last conversation laid out like a
WhatsApp chat, the artifacts JARVIS made, and a **Vault** for things the user tells it to keep.
JARVIS also gains **WhatsApp from the user's own number**. Everything must feel premium, with
fluid, interruptible, glitch-free motion.

The current HUD stays as **Full view** (button in the panel header), opened in Edge as today.

## 2. Architecture

```
Electron island window (desktop/)  ── WebSocket /ws ──►  Python JARVIS (unchanged core)
        │ loads http://127.0.0.1:8765/island                 │
        │                                                     ├─ SQLite: messages/chats, vault, actions
        └─ "Full view" → Edge --app (existing HUD)            └─ send_whatsapp ─► WhatsApp helper (Node, Baileys)
```

- **`desktop/` (new, Electron).** One window: fixed size (560×660: room for the widest island plus its split bubble, the panel,
  and the glow around them), transparent, frameless,
  always on top (`'screen-saver'` level, re-asserted after the Start menu, taskbar and UAC),
  `focusable: false` + `showInactive()` so it never steals focus, `type: 'toolbar'` so it stays
  out of Alt+Tab, `backgroundThrottling: false`. Transparent areas pass clicks through
  (`setIgnoreMouseEvents(true, {forward: true})`), switched off over the island via IPC from the
  page. The window never resizes or moves during an animation: the island morphs **inside** it.
  The window becomes focusable only while the panel's text box is in use.
- **`python -m jarvis`** starts the backend, then the Electron island instead of Edge. If
  Electron exits unexpectedly it is restarted once; if the user quits it from its tray icon it
  stays closed. Full view keeps working either way.
- **Island page (`jarvis/ui/island.html`, `island.js`, `island.css`).** Served by the existing
  FastAPI app, speaking the existing `/ws` protocol (`state`, `history`, `chats`, `confirm`,
  `banner`, `user_text`, …) plus the new messages in §5. The orb is the existing
  `jarvis/ui/core.js` glow ring, shared with the HUD (core.js gets a hue/filter hook per state;
  no copy).
- **WhatsApp helper (`desktop/wa/`, Node + `@whiskeysockets/baileys`).** A small local process
  on 127.0.0.1 with a random per-run token that Python reads from `data/wa/port`. It links the
  user's own WhatsApp by QR once (WhatsApp Web protocol, no browser). Session in
  `data/wa_auth/` (gitignored; never committed, never sent anywhere).
- **Vault:** a `vault` table plus `data/vault/` for copied files. The folder joins the content
  index, so "what's in my vault about…" works through the existing notes search.
- **Artifacts:** no new store. The list is built from the action log (files created or edited,
  Claude Code commits) plus answers the user saves with "save this" (a new `save_answer` tool
  that writes a Markdown file under `data/artifacts/` and logs it).

## 3. The island (collapsed)

Pure black pill, 38 px tall, radius 19, width **follows its content** (120–460 px). Orb
(28 px) left, mic (28 px) right, content between them.

| State | Orb | Middle of the island |
|---|---|---|
| Idle | full spectrum, slow | a ticker cycling every 4.5 s: next class, weather, nearest deadline, important mail (from the briefing sources; skips any not connected) |
| Listening | hue shifts cool (blue-cyan), **no pulse** | "Listening", then the user's words blurring in; a 7-bar blue level meter beside the mic, driven by the real mic level |
| Thinking | hue shifts warm (amber-pink), fast spin | shimmering "Thinking"; a **comet of light circles the island's edge in an unbroken loop** until thinking ends, then fades |
| Speaking | full spectrum | JARVIS's words; a spectrum ring runs around the edge |

- **Hover** (after 120 ms): peek. The island grows to ~64 px and shows the last reply's first
  line.
- **Press:** a slight squish (velocity kick on the width/height springs).
- **Mic:** hold to talk (push-to-talk), tap to toggle; the same as Right Ctrl.
- **Confirmations** (WhatsApp send, file edits, devices…): the island grows to a mid card
  (~400×122) with the question, the full message text, and Cancel / Send (or Allow). Voice
  yes/no still works.
- **Live activities:** a running timer, a Claude Code task or a WhatsApp send splits off as a
  small round bubble beside the island (gooey split), with a progress ring.
- **Notifications** (reminders, WhatsApp replies): drop out of the island as a card, stay
  ~3.5 s, then are pulled back in.
- **Drag a file or text onto the island:** "Keep in vault?" mid card.
- **Fullscreen apps** (games, video, presentations): the island **tucks away** (slides up out of
  sight). "Hey Jarvis", a reminder or a confirmation brings it back.

## 4. The panel (expanded)

440×600, radius 32, deep tinted glass (on Windows the desktop behind can't be blurred through a
transparent frameless window, so "glass" is a dark tint `rgba(20,20,26,.9)`, faint noise, a 1 px
inner highlight and a large soft shadow). The orb glides from the pill into the header.

- **Header:** orb, "JARVIS", status line (state · route), Full view, collapse.
- **Glance cards:** a horizontally swipeable row: weather, next class, mail, due soon.
- **Tabs (segmented control, sliding indicator): Chat · Artifacts · Vault.**
  - **Chat:** recent-chat chips across the top (existing chats; tap one to load it), then the
    selected conversation as WhatsApp bubbles: the user's on the right (accent gradient), JARVIS on
    the left; timestamps and ticks; a mic marker on voice turns; WhatsApp sends shown as a
    green-edged quote card; sources as chips. New turns animate in.
  - **Artifacts:** a two-column grid of tiles with real previews (first lines of a document, a
    diff for commits, an image thumbnail) and a caption (name · kind · when). Click opens it in its
    default app; a tile whose file is gone says "moved or deleted".
  - **Vault:** a search box, a drop zone hint, and items (note, link, file) with icon, title and
    when. Click opens; remove sends it to trash (undoable).
- **Input bar:** text box and mic.
- Esc, the collapse button or clicking outside collapses it.

## 5. Backend additions

- **Vault tools:** `keep(text|path|url, title?)`, `find_in_vault(query)`,
  `remove_from_vault(id)`. Files are **copied** in (the original is never moved); above 100 MB it
  asks first. Remove moves to `data/trash` with the usual undo.
- **`save_answer(title)`**: saves the last answer as Markdown under `data/artifacts/`.
- **WebSocket messages:** `vault` (list), `artifacts` (list), `glance` (ticker/cards data),
  `chat_load` (request a chat's messages), `fullscreen` (true/false), `mic_level` (0–1, ~15/s,
  only while listening).
- **Fullscreen detection:** `SHQueryUserNotificationState` via ctypes every 2 s;
  busy/fullscreen/presentation → `fullscreen: true`.
- **WhatsApp:** `send_whatsapp(to, text)` goes through the helper when it's linked, else the
  existing n8n webhook. `to` may be a name: it is matched against the contacts the helper syncs;
  several matches → JARVIS asks which; none → it asks for the number and offers to remember it.
  **Always confirmed** (the card shows recipient and full text). Blocked on the phone/WhatsApp
  channels (as today). At most 20 sends an hour. Incoming messages are surfaced **only** as
  replies in chats JARVIS sent to in the last 24 hours; nothing else is read or stored.
  New: `whatsapp link` CLI command and a panel button that shows the QR.

## 6. Motion

Springs from Apple's formula (mass 1; stiffness = (2π/d)², damping = (1−bounce)·4π/d), all
interruptible (a new target continues from the current velocity):

| Use | duration / bounce |
|---|---|
| Expand pill → panel / mid card | 0.5 s / 0.15 |
| Collapse | 0.42 s / 0 |
| Width-follows-content, peek, tabs, press | 0.3 s / 0.1 |
| Notification drop, bubble split, un-tuck | 0.55 s / 0.3 |

- Expand: the shell springs first and the content follows ~80 ms later (fade + blur 6 px→0 +
  scale .97→1). Collapse: content out first, then the shell.
- Only one element changes size (the shell, `contain: layout paint`); its content is laid out at
  final size and clipped, so nothing reflows. Everything else animates `transform`/`opacity`.
- Windows "reduce animations" → springs become short fades; the ticker and comet stop.

## 7. Errors

- Backend unreachable: orb dims, island says "Starting…" / "Offline · click to retry", auto-reconnects.
- WhatsApp not linked / logged out: "WhatsApp isn't linked, sir" + show-QR button. Failure is
  reported plainly; JARVIS never says "sent" unless the helper confirmed it.
- Multi-monitor / DPI: island on the primary display, re-centred on display changes, whole pixels.
- Island window crash: restarted once (see §2).

## 8. Testing

- **pytest (no network):** vault tools (copy, size check, remove → trash → undo), artifact
  listing (including missing files), `save_answer`, chat loading, glance data, fullscreen state
  mapping (ctypes mocked), WhatsApp via a fake helper (confirm, name match / ambiguous / unknown,
  failure, 20/hour cap, remote blocked, fallback to n8n).
- **node:test** for the helper: contact matching and send against a fake Baileys socket.
- **Playwright** on `/island` in a normal browser: every state and moment, no console errors,
  and a frame-time check: over expand/collapse/peek, more than 1% of frames above 20 ms fails.
- **Live with the user:** QR link and a message to themselves, fullscreen tuck with a video, a
  voice round trip, a drag into the vault.

## 9. Decisions (user, 2026-10-23)

1. Floating island above every app; not a notch (tried, user preferred the floating pill).
2. Keep both UIs: island everyday, HUD as Full view.
3. WhatsApp from the user's own number via Baileys (unofficial; small ban risk accepted), always
   confirmed.
4. All four extra groups in v1: living island, signature motion, panel extras, polite behaviour.
5. Fullscreen apps: tuck away.
6. Listening and thinking clearly different (colour, words, meter vs. comet); the orb doesn't pulse.
7. Thinking light: one comet looping around the edge until thinking ends.
8. Idle island is never empty: the ticker.

## 10. Later (not in v1)

Music / media live activity, swipe gestures on the island, multi-monitor follow-the-cursor,
desktop-blur through a framed acrylic window, a WhatsApp inbox view, reading other apps'
notifications, themes.
