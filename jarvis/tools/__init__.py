"""Tools the model can call. A tool is a plain function fn(ctx, **args) -> (result, undo)."""
import asyncio
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from jarvis import db

MAX_RESULT = 4000
DECLINED = "The user declined."


@dataclass
class Tool:
    name: str
    fn: Callable
    description: str
    parameters: dict
    confirm: bool = False
    describe: Callable[[dict], str] | None = None   # verb phrase for the question: "close Chrome"
    readonly: bool = False    # changes nothing: never needs confirming, even in a tainted turn
    private: bool = False     # returns file contents: the rest of the turn stays on `local`
    untrusted: bool = False   # returns outside text that may carry instructions: taints the turn
    exports: bool = False     # its input leaves the laptop (internet, or cloud prompts)
    local_only: bool = False  # only changes JARVIS's own undoable records (plan, tasks...)
    # per-call decisions (e.g. hardware: "start" asks, "stop" never does)
    needs_confirm: Callable[[object, dict], bool] | None = None
    remote_ok: Callable[[object, dict], bool] | None = None
    limit: int = MAX_RESULT   # result size cap (characters)


@dataclass
class Ctx:
    cfg: object
    conn: object
    confirm: Callable[[str], Awaitable[bool]]
    emit: Callable[[dict], Awaitable[None]]
    agent: str | None = None              # set when a sub-agent is calling
    allowed: list[str] | None = None      # the sub-agent's tool list
    actions: list[dict] = field(default_factory=list)   # {name, arguments, result} for history
    private: bool = False     # file contents were read this turn
    tainted: bool = False     # untrusted text was read this turn: side effects need a yes
    remote: str | None = None # "phone"/"whatsapp": nothing with a side effect from there


TOOLS: dict[str, Tool] = {}


def tool(description: str, confirm: bool = False, describe=None, readonly: bool = False,
         private: bool = False, untrusted: bool = False, max_chars: int = MAX_RESULT,
         exports: bool = False, local_only: bool = False, needs_confirm=None, remote_ok=None,
         **params: str):
    """Register fn as a tool. params: name="type: description"; a leading '?' makes it optional."""
    def wrap(fn):
        props, required = {}, []
        for name, spec in params.items():
            optional = spec.startswith("?")
            typ, _, desc = spec.lstrip("?").partition(":")
            props[name] = {"type": typ.strip(), "description": desc.strip()}
            if not optional:
                required.append(name)
        TOOLS[fn.__name__] = Tool(fn.__name__, fn, description,
                                  {"type": "object", "properties": props, "required": required},
                                  confirm=confirm, describe=describe, readonly=readonly,
                                  private=private, untrusted=untrusted, exports=exports,
                                  local_only=local_only, needs_confirm=needs_confirm,
                                  remote_ok=remote_ok,
                                  limit=max_chars)
        return fn
    return wrap


def schemas(names: list[str]) -> list[dict]:
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": t.parameters}}
            for n in names if (t := TOOLS.get(n))]


def _short(args: dict) -> str:
    return " ".join(str(v) for v in args.values())[:60]


def _record(ctx: Ctx, name: str, args: dict, result: str, status: str, undo) -> dict:
    aid = db.log_action(ctx.conn, name, args, result, status, undo, ctx.agent)
    ctx.actions.append({"name": name, "arguments": json.dumps(args), "result": result[:500]})
    return {"type": "action", **db.get_action(ctx.conn, aid)}


async def _log(ctx: Ctx, name: str, args: dict, result: str, status: str, undo) -> None:
    await ctx.emit(_record(ctx, name, args, result, status, undo))


def _outcome(fut: asyncio.Future, limit: int = MAX_RESULT) -> tuple[str, str, dict | None]:
    """(result, status, undo) from a finished tool call."""
    if fut.cancelled():
        return "Error: cancelled", "failed", None
    if (e := fut.exception()) is not None:
        return f"Error: {e}", "failed", None
    out = fut.result()
    result, undo_info = out if isinstance(out, tuple) else (out, None)
    return str(result)[:limit], "done", undo_info


def _exports(ctx: Ctx, t: Tool, args: dict) -> bool:
    if t.name == "delegate":   # only an agent pinned to the local model may see private text
        a = ctx.cfg.agents.get(str(args.get("agent")))
        return not (a and a.route == "local" and not a.fallback)
    return t.exports


async def run(ctx: Ctx, call: dict) -> str:
    name = call["name"]
    if ctx.agent and (name == "delegate" or name not in (ctx.allowed or [])):  # one level deep
        return f"Error: {name} is not available to the {ctx.agent} agent."
    t = TOOLS.get(name)
    if t is None:
        return f"Error: there is no tool named {name}."
    try:
        args = json.loads(call["arguments"] or "{}")
        assert isinstance(args, dict)
    except (ValueError, AssertionError):
        return "Error: the tool arguments were not valid JSON."
    # Sub-agents and tainted turns ask before anything that changes something; looking
    # (search, read) doesn't ask: a researcher asking before every search was unusable.
    try:
        dynamic = t.needs_confirm(ctx, args) if t.needs_confirm else None
        remote_ok = bool(t.remote_ok and t.remote_ok(ctx, args))
    except Exception as e:   # e.g. an unknown device: say so before asking anything
        result = f"Error: {e}"
        await _log(ctx, name, args, result, "failed", None)
        return result
    if ctx.remote:
        allow = (getattr(ctx.cfg, "remote", {}) or {}).get("allow", [])
        if not t.readonly and name not in allow and not remote_ok:
            return (f"Error: {name} needs the user at the desk; it can't be done over "
                    f"{ctx.remote}. Tell them that.")
        if t.private and ctx.cfg.content_private:
            return f"Error: the user's private files can't be read over {ctx.remote}."
    if ctx.private and ctx.cfg.content_private and _exports(ctx, t, args):
        return (f"Error: this request used the user's private files, so {name} can't run in "
                "it: nothing from it may reach the internet or cloud models. The user can ask "
                "for that separately.")
    # a tool that decides per call (dynamic is True/False) also decides for tainted turns:
    # stopping hardware must never wait for a yes
    if t.confirm or dynamic or (ctx.agent and not t.readonly) or (
            ctx.tainted and not t.readonly and not t.local_only and dynamic is None):
        try:  # describe may resolve the target (e.g. the window) and fail before we ask
            phrase = t.describe(args) if t.describe else f"run {name} {_short(args)}".strip()
        except Exception as e:
            result = f"Error: {e}"
            await _log(ctx, name, args, result, "failed", None)
            return result
        q = (f"The {ctx.agent} agent wants to {phrase}. Allow, sir?" if ctx.agent
             else f"{phrase[0].upper()}{phrase[1:]}, sir?")
        if not await ctx.confirm(q):
            await _log(ctx, name, args, DECLINED, "declined", None)
            return DECLINED
    await ctx.emit({"type": "tool_start", "name": name})   # the island's live-activity bubble
    # the UI's neural view lights the module that's working (tools/<module>.py)
    await ctx.emit({"type": "tool", "name": name, "module": t.fn.__module__.rsplit(".", 1)[-1]})
    if inspect.iscoroutinefunction(t.fn):
        task = asyncio.ensure_future(t.fn(ctx, **args))
    else:
        task = asyncio.ensure_future(asyncio.to_thread(t.fn, ctx, **args))
    try:
        await asyncio.wait([task])
    except asyncio.CancelledError:
        # The turn was cancelled, but a thread can't be stopped: the action still happens,
        # so log it (and its undo) when it finishes. A coroutine tool (delegate) is cancelled.
        if inspect.iscoroutinefunction(t.fn):
            task.cancel()
        task.add_done_callback(lambda f: _record(ctx, name, args, *_outcome(f)))
        raise
    result, status, undo_info = _outcome(task, t.limit)
    if status == "done":
        if t.private and ctx.cfg.content_private:
            ctx.private = True
        if t.untrusted:
            ctx.tainted = True
            result = f'<untrusted source="{name}">\n{result}\n</untrusted>'
    await _log(ctx, name, args, result, status, undo_info)
    return result


def undo(conn, action_id: int) -> str:
    a = db.get_action(conn, action_id)
    if not a or a["status"] != "done" or not a["undo"]:
        raise ValueError("There is nothing to undo for that action.")
    u = a["undo"]
    if u["kind"] == "delete_memory":
        db.delete_memory(conn, u["id"])
    elif u["kind"] == "restore_memory":
        db.restore_memory(conn, u["id"], u["text"])
    elif u["kind"] in devices.KINDS:
        devices.undo(u)
    elif u["kind"] in google_tools.KINDS:
        google_tools.undo(u)
    elif u["kind"] in day.KINDS:
        if u.get("table", "timetable") not in day.TABLES:
            raise ValueError("bad undo record")
        day.undo(conn, u)
    elif u["kind"] in claude.KINDS:
        claude.undo(u)
    elif u["kind"] in vault.KINDS:
        vault.undo(conn, u, Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent)
    elif u["kind"] in edit.KINDS:
        edit.undo(u, Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent)
    else:
        from jarvis.tools import windows
        windows.undo(u)
    db.update_action(conn, action_id, status="undone")
    return f"Undid {a['tool']} {_short(a['args'])}".strip() + "."


_YES = {"yes", "yeah", "yep", "sure", "confirm", "okay", "ok", "affirmative"}
_NO = {"no", "nope", "cancel", "stop", "don't", "dont", "abort", "negative"}


def parse_yes_no(text: str) -> bool:
    t = text.lower()
    words = set(re.findall(r"[a-z']+", t))
    if words & _NO:
        return False
    return bool(words & _YES) or "do it" in t or "go ahead" in t


UNDO_WINDOW = timedelta(minutes=30)


@tool("Undo the user's most recent action (open, window move, memory change), if possible.")
def undo_last(ctx):
    """Only the latest change counts: skipping past an action that can't be undone would
    undo something older the user didn't mean."""
    since = (datetime.now() - UNDO_WINDOW).isoformat(timespec="seconds")
    for a in db.recent_actions(ctx.conn, 50):
        if a["ts"] < since:
            break
        t = TOOLS.get(a["tool"])
        if (t and t.readonly) or a["tool"] == "undo_last" or a["status"] in ("declined", "failed"):
            continue
        if a["status"] == "done" and a["undo"]:
            return undo(ctx.conn, a["id"]), None
        return f"I can't undo {a['tool']} ({a['status']}).", None
    return "There is nothing recent to undo.", None


from jarvis.tools import apps, briefing, claude, day, delegate, devices, edit, github, google_tools, outbound, files, memory, notes, vault, web, windows  # noqa: E402,F401  (registers their tools)
from jarvis import artifacts  # noqa: E402,F401  (registers save_answer)
