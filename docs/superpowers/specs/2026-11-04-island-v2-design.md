# JARVIS Island v2: design notes

Status: built 2026-11-04 under the user's standing authority ("do whatever you feel is
correct"), after the user shared a promo video of a Windows "dynamic island" app as the
direction. We build our own look in the existing island style; nothing is copied from that app.

## What's new

- **Home tab** (first of four: Home · Chat · Artifacts · Vault) with three cards:
  - **Claude Code:** elapsed time, step (Reading / Editing / Testing · n of 4), a progress
    ring. A strip below shows the project, the file being edited with its first changed line
    (red/green), and a Read → Edit → Test → Done bar.
  - **Music:** the Windows media session (any player): title, artist, cover, prev/play/next.
  - **Focus:** a 25-minute timer kept by the backend, so every window agrees.
  - **Quick actions** on the clipboard: Explain, Check spelling, Translate, Summarize. They
    go in as a typed turn and the panel switches to Chat.
- **Pill, all at once:** the song splits off as a spinning cover bubble on the left (the right
  one stays for running jobs); a focus countdown takes the middle in purple.
- **Volume HUD:** a system volume change stretches the island into a bar for ~1.6 s.
- **Ctrl+Alt+J** opens or closes the panel from anywhere (Electron global shortcut).

## Backend

- `claude -p` now runs with `--output-format stream-json --verbose`; tool-use events map to
  `claude_live` messages (`start`, `read`, `edit` with file/old/new, `test`, `done`/`failed`).
  The final `result` event is the reply, as before. stderr drains on its own thread.
- `jarvis/desk.py`: `media()` / `media_control()` via `winrt-Windows.Media.Control`;
  `watch_volume()` via pycaw on its own COM thread (5 Hz, follows a new default device).
- Hub: `watch_media` (1 s; the cover only when the song changes), `set_focus`, and new pages
  get `claude_live` (if any), `media` and `focus` before the vault/artifacts/glance messages.
- WebSocket in: `media` {action: toggle|next|prev}, `focus` {minutes; 0 stops}.
  Out: `claude_live`, `media`, `focus`, `volume` {level, muted}.

## Decisions

1. The orb stays JARVIS's face (approved 2026-10-23); no robot eyes. Cost if wrong: a
   face is a contained swap in `#orb`.
2. Music bubble on the **left**, since the right bubble already means "a job is running".
3. Focus lives in the backend, not the page, so a reload or a second window keeps it.
4. Quick actions read the clipboard through Electron (sandboxed preload → main), falling
   back to `navigator.clipboard` in a browser.
5. Later: "drag the assistant onto anything on screen" (needs screen capture + vision),
   clipboard and file tabs, a voice tool for focus.

## Revision, evening of 2026-11-04: the wide panel

After a side-by-side with the reference clip, the panel moved from a tall 440×600 column to a
wide 620 px panel:

- **Height per view:** Home and Code are 300 tall, so they read at a glance. Chat, Artifacts and
  Vault are 540, since you read them. A tab change springs the island to the new height, and
  the panel tracks the shell's height while it moves.
- **Header:** one 52 px line. JARVIS and its status on the left; the date, time and weather
  chips in the centre; Full view and Collapse on the right. The orb shrinks to fit.
- **Dock:** the tabs are icons in a bottom dock, and the selected one opens into a labelled
  pill. The mic sits at the dock's right. The text box shows only in Chat.
- **Code tab:** Claude Code's task is shown big, with the timer, the diff and the steps. While a
  task runs and the tab is open, the panel tints warm. The Home card opens this tab.
- **Cards:** bigger numbers and rounder corners. The song's cover sits in the Music card's
  corner and also blurs into its background.
- **Electron:** the window is 760 wide, to fit the panel.

Lyrics built 2026-11-11 (LRCLIB synced lines, word highlight estimated). Still not built: "drag JARVIS onto
anything on screen" (needs screen capture plus a vision model).
