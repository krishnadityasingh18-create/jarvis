"""The user's day: timetable, tasks, reminders and today's plan. Local, undoable."""
from datetime import datetime, timedelta

from jarvis.tools import tool

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def now() -> datetime:
    return datetime.now()


def _day(day) -> int:
    s = str(day).strip().lower()
    if s.isdigit() and int(s) < 7:
        return int(s)
    for i, d in enumerate(DAYS):
        if len(s) >= 3 and d.lower().startswith(s):
            return i
    raise ValueError(f"'{day}' isn't a day of the week")


def _hhmm(t: str) -> str:
    try:
        return datetime.strptime(str(t).strip(), "%H:%M").strftime("%H:%M")
    except ValueError:
        raise ValueError(f"'{t}' isn't a time like 09:00") from None


def _row(conn, table: str, rid: int) -> dict:
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (int(rid),)).fetchone()
    if not row:
        raise ValueError(f"there is no {table[:-1] if table != 'timetable' else 'class'} "
                         f"with id {rid}")
    return dict(row)


def today_plan(conn) -> str | None:
    row = conn.execute("SELECT text FROM plans WHERE day=?", (now().date().isoformat(),)).fetchone()
    return row[0] if row else None


@tool("Add a class to the user's weekly timetable.",
      day="string: weekday, e.g. Monday", start="string: HH:MM", end="string: HH:MM",
      subject="string: the subject", room="?string: room or place")
def add_class(ctx, day, start, end, subject, room=""):
    d, s, e = _day(day), _hhmm(start), _hhmm(end)
    rid = ctx.conn.execute("INSERT INTO timetable(day, start, end, subject, room) "
                           "VALUES(?,?,?,?,?)", (d, s, e, subject, room)).lastrowid
    return f"Added [{rid}] {DAYS[d]} {s}-{e} {subject}.", {"kind": "delete_row",
                                                          "table": "timetable", "id": rid}


@tool("Remove a class from the timetable by its [id].", class_id="integer: the class id")
def remove_class(ctx, class_id):
    row = _row(ctx.conn, "timetable", class_id)
    ctx.conn.execute("DELETE FROM timetable WHERE id=?", (row["id"],))
    return f"Removed {DAYS[row['day']]} {row['subject']}.", {"kind": "restore_row",
                                                              "table": "timetable", "row": row}


def timetable_text(conn, only_day: int | None = None) -> str:
    rows = conn.execute("SELECT * FROM timetable ORDER BY day, start").fetchall()
    out, last = [], None
    for r in rows:
        if only_day is not None and r["day"] != only_day:
            continue
        if r["day"] != last and only_day is None:
            out.append(f"{DAYS[r['day']]}:")
            last = r["day"]
        out.append(f"- [{r['id']}] {r['start']}-{r['end']} {r['subject']}"
                   + (f" ({r['room']})" if r["room"] else ""))
    return "\n".join(out)


@tool("Show the user's weekly timetable.", readonly=True)
def list_timetable(ctx):
    return timetable_text(ctx.conn) or "The timetable is empty.", None


def _due(due: str) -> str:
    try:
        return datetime.fromisoformat(due).strftime("%a %d %b")
    except ValueError:
        return due


@tool("Add a task or deadline.", title="string: what to do",
      due="?string: due date YYYY-MM-DD, if there is one")
def add_task(ctx, title, due=""):
    if due:
        datetime.fromisoformat(due)  # raises if not a date
    rid = ctx.conn.execute("INSERT INTO tasks(title, due) VALUES(?,?)", (title, due)).lastrowid
    return f"Added task [{rid}] {title}" + (f", due {_due(due)}." if due else "."), \
        {"kind": "delete_row", "table": "tasks", "id": rid}


@tool("Mark a task as done by its [id].", task_id="integer: the task id")
def complete_task(ctx, task_id):
    row = _row(ctx.conn, "tasks", task_id)
    ctx.conn.execute("UPDATE tasks SET done=1 WHERE id=?", (row["id"],))
    return f"Done: {row['title']}.", {"kind": "restore_row", "table": "tasks", "row": row}


def tasks_text(conn, within_days: int | None = None) -> str:
    rows = conn.execute("SELECT * FROM tasks WHERE done=0 ORDER BY due='' , due").fetchall()
    out = []
    for r in rows:
        if within_days is not None and r["due"] and \
                r["due"] > (now().date() + timedelta(days=within_days)).isoformat():
            continue
        late = r["due"] and r["due"] < now().date().isoformat()
        out.append(f"- [{r['id']}] {r['title']}" + (f" (due {_due(r['due'])})" if r["due"] else "")
                   + (" OVERDUE" if late else ""))
    return "\n".join(out)


@tool("List the user's open tasks and deadlines.", readonly=True)
def list_tasks(ctx):
    return tasks_text(ctx.conn) or "No open tasks.", None


@tool("Set a reminder that JARVIS speaks at the given time.",
      at="string: local date and time, YYYY-MM-DDTHH:MM", text="string: what to remind")
def remind(ctx, at, text):
    try:
        when = datetime.fromisoformat(at)
    except ValueError:
        raise ValueError(f"'{at}' isn't a date and time like 2026-10-12T09:15") from None
    if when < now() - timedelta(minutes=1):
        raise ValueError("that time has already passed")
    rid = ctx.conn.execute("INSERT INTO reminders(at, text) VALUES(?,?)",
                           (when.isoformat(timespec="minutes"), text)).lastrowid
    return f"I'll remind you at {when:%H:%M} on {when:%a %d %b}: {text}", \
        {"kind": "delete_row", "table": "reminders", "id": rid}


@tool("Cancel a reminder by its [id].", reminder_id="integer: the reminder id")
def cancel_reminder(ctx, reminder_id):
    row = _row(ctx.conn, "reminders", reminder_id)
    ctx.conn.execute("DELETE FROM reminders WHERE id=?", (row["id"],))
    return f"Cancelled the reminder: {row['text']}.", {"kind": "restore_row",
                                                        "table": "reminders", "row": row}


@tool("Save today's plan (the whole plan, replacing the previous one). Use it after a "
      "briefing and whenever the user changes the plan.", text="string: the full plan")
def set_plan(ctx, text):
    day = now().date().isoformat()
    old = today_plan(ctx.conn)
    ctx.conn.execute("INSERT OR REPLACE INTO plans(day, text) VALUES(?,?)", (day, text))
    return "Today's plan is saved.", {"kind": "restore_plan", "day": day, "text": old}


def undo(conn, u: dict) -> None:
    if u["kind"] == "delete_row":
        conn.execute(f"DELETE FROM {u['table']} WHERE id=?", (u["id"],))
    elif u["kind"] == "restore_row":
        cols = ", ".join(u["row"])
        conn.execute(f"INSERT OR REPLACE INTO {u['table']}({cols}) VALUES "
                     f"({', '.join('?' * len(u['row']))})", tuple(u["row"].values()))
    elif u["kind"] == "restore_plan":
        if u["text"] is None:
            conn.execute("DELETE FROM plans WHERE day=?", (u["day"],))
        else:
            conn.execute("INSERT OR REPLACE INTO plans(day, text) VALUES(?,?)",
                         (u["day"], u["text"]))


KINDS = {"delete_row", "restore_row", "restore_plan"}
TABLES = {"timetable", "tasks", "reminders"}
