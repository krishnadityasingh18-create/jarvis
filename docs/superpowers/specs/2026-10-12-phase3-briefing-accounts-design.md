# JARVIS Phase 3: Daily Briefing, Day Plan and Accounts — Design

Status: self-approved under the user's standing permission (2026-10-12: "move forward with
the phases without my approval"). Decisions are listed in §9 for the user's review.

## 1. Goal

A morning briefing (spoken summary, detail on screen) covering today's classes, deadlines,
reminders, weather, calendar, important email and GitHub, followed by a proposed plan for
the day that the user adjusts by voice. Reminders the user sets fire on time. Google and
GitHub accounts connect through their own logins (OAuth / `gh`), never passwords.

## 2. Scope

In: timetable, tasks/deadlines, reminders (+ scheduler), today's plan, current date/time in
the prompt, weather, `daily_briefing`, a once-a-day morning briefing, Google (Gmail read +
drafts, Calendar read + add, Drive search) behind `python -m jarvis accounts connect google`,
GitHub through the logged-in `gh` CLI.

Out: sending email (drafts only; the user sends), WhatsApp and calls (Phase 4), hardware
(Phase 5), automatic timetable import from the university portal (manual entry by voice).

## 3. Local day data (works with no accounts)

```sql
CREATE TABLE timetable (id INTEGER PRIMARY KEY, day INTEGER, start TEXT, end TEXT,
  subject TEXT, room TEXT);                      -- day 0 = Monday; times "HH:MM"
CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT, due TEXT, done INTEGER DEFAULT 0);
CREATE TABLE reminders (id INTEGER PRIMARY KEY, at TEXT, text TEXT, done INTEGER DEFAULT 0);
CREATE TABLE plans (day TEXT PRIMARY KEY, text TEXT);   -- day = YYYY-MM-DD
```

Tools (all undoable, none needs confirmation): `add_class(day, start, end, subject, room)`,
`remove_class(class_id)`, `list_timetable()`, `add_task(title, due)`, `complete_task(task_id)`,
`list_tasks()`, `remind(at, text)` (`at` = ISO local datetime), `cancel_reminder(id)`,
`set_plan(text)` (today's plan, replaced whole).

The system prompt gains "Now: Monday 12 October 2026, 07:30 (Asia/Kolkata)" and, when set,
"Today's plan: …", so relative times ("in an hour", "tomorrow") and plan edits by voice work.

## 4. Briefing

- `daily_briefing()` (read-only, untrusted) gathers, each part optional and failing softly:
  date; weather (Open-Meteo, `[briefing] latitude/longitude`, default SRM KTR 12.82, 80.04);
  today's classes; tasks overdue or due in 7 days; today's reminders; today's plan if any;
  Google Calendar events today; up to 5 unread important Gmail messages (sender, subject);
  GitHub: open PRs/issues assigned to the user and review requests (`gh search`).
- The model turns it into: 1–2 spoken sentences, then on screen the full briefing and a
  proposed plan for the day, and calls `set_plan` with that plan.
- **Morning briefing:** `[briefing] time = "07:30"`, `enabled = true`. The hub's scheduler
  (every 30 s) starts a briefing turn ("Give me my morning briefing.") the first time it runs
  after that time on a day it hasn't briefed yet (only while JARVIS is running; recorded in
  `plans`/a `briefed` marker so it happens once a day). This is the "briefings and reminders
  I have set up" exception to act-only-on-command.
- **Reminders:** the same scheduler speaks "Reminder, sir: …" and shows a banner when one is
  due, then marks it done. Missed ones (JARVIS was off) fire once at startup, marked "late".

## 5. Accounts

- **Google:** `python -m jarvis accounts connect google` runs the installed-app OAuth flow
  (browser consent) with the user's own client file `data/google_client.json`; the token is
  stored in Windows Credential Manager (`jarvis/google_token`), refreshed automatically.
  Scopes: `gmail.readonly`, `gmail.compose` (drafts), `calendar.events`, `drive.readonly`.
  Tools appear only when connected:
  `gmail_search(query, n)`, `gmail_read(message_id)`, `gmail_draft(to, subject, body)`
  (confirm; undo deletes the draft), `calendar_events(days)`, `calendar_add(title, start,
  end)` (confirm; undo deletes the event), `drive_search(query)`. Email/Drive/Calendar text is
  untrusted (taints the turn).
- **GitHub:** via the logged-in `gh` CLI (no new login): `github_activity()` (read-only)
  lists assigned issues/PRs, review requests and unread notifications count.
- `python -m jarvis accounts list` shows what is connected.

## 6. Errors

Every briefing part fails soft ("weather unavailable"). Google not connected → its tools are
absent and the briefing says "Google isn't connected yet". Token revoked → banner "Google
needs reconnecting: python -m jarvis accounts connect google". `gh` logged out → GitHub part
skipped.

## 7. Testing

Unit tests with fakes: each local tool + undo; datetime/plan in the system prompt; reminder
scheduler (due, not yet due, late at startup, once only); morning briefing fires once per day
after the set time; `daily_briefing` with fake weather/Google/gh; Google tools with a fake
service object; gmail_draft/calendar_add confirm + undo; accounts CLI with a fake flow. Live:
briefing with real weather, timetable and GitHub.

## 8. Cost

₹0. Open-Meteo, Google APIs (personal quota) and `gh` are free.

## 9. Decisions made under standing permission

1. Timetable entered by voice/text (no portal scraping, no file format to learn).
2. Email: read and draft only; sending stays with the user (outward-facing).
3. Calendar events and drafts need confirmation and are undoable.
4. The morning briefing only runs while JARVIS is running, once a day, after the set time.
5. Weather for SRM Kattankulathur by default (config).
6. GitHub through the existing `gh` login instead of a new token.
7. Google needs the user's own OAuth client (Google Cloud console) — a login step on the
   user's list; everything else works without it.
