"""Tools the model can call. A tool is a plain function fn(ctx, **args) -> (result, undo)."""
import asyncio
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

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


@dataclass
class Ctx:
    cfg: object
    conn: object
    confirm: Callable[[str], Awaitable[bool]]
    emit: Callable[[dict], Awaitable[None]]
    agent: str | None = None              # set when a sub-agent is calling
    allowed: list[str] | None = None      # the sub-agent's tool list
    actions: list[str] = field(default_factory=list)   # "tool args → status" for history


TOOLS: dict[str, Tool] = {}


def tool(description: str, confirm: bool = False, describe=None, **params: str):
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
                                  confirm, describe)
        return fn
    return wrap


def schemas(names: list[str]) -> list[dict]:
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": t.parameters}}
            for n in names if (t := TOOLS.get(n))]


def _short(args: dict) -> str:
    return " ".join(str(v) for v in args.values())[:60]


async def _log(ctx: Ctx, name: str, args: dict, result: str, status: str, undo) -> None:
    aid = db.log_action(ctx.conn, name, args, result, status, undo, ctx.agent)
    ctx.actions.append(f"{name} {_short(args)}".strip() + f" → {status}")
    await ctx.emit({"type": "action", **db.get_action(ctx.conn, aid)})


async def run(ctx: Ctx, call: dict) -> str:
    name = call["name"]
    if ctx.allowed is not None and name not in ctx.allowed:
        return f"Error: {name} is not available to the {ctx.agent} agent."
    t = TOOLS.get(name)
    if t is None:
        return f"Error: there is no tool named {name}."
    try:
        args = json.loads(call["arguments"] or "{}")
        assert isinstance(args, dict)
    except (ValueError, AssertionError):
        return "Error: the tool arguments were not valid JSON."
    if t.confirm or ctx.agent:
        phrase = t.describe(args) if t.describe else f"run {name} {_short(args)}".strip()
        q = (f"The {ctx.agent} agent wants to {phrase}. Allow, sir?" if ctx.agent
             else f"{phrase[0].upper()}{phrase[1:]}, sir?")
        if not await ctx.confirm(q):
            await _log(ctx, name, args, DECLINED, "declined", None)
            return DECLINED
    try:
        if inspect.iscoroutinefunction(t.fn):
            out = await t.fn(ctx, **args)
        else:
            out = await asyncio.to_thread(t.fn, ctx, **args)
        result, undo_info = out if isinstance(out, tuple) else (out, None)
        status = "done"
    except Exception as e:  # CancelledError is not an Exception: it propagates
        result, undo_info, status = f"Error: {e}", None, "failed"
    result = str(result)[:MAX_RESULT]
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


@tool("Undo the most recent action that can be undone (open, window move, memory change).")
def undo_last(ctx):
    for a in db.recent_actions(ctx.conn, 50):
        if a["status"] == "done" and a["undo"]:
            return undo(ctx.conn, a["id"]), None
    return "There is nothing to undo.", None


from jarvis.tools import memory  # noqa: E402,F401  (registers its tools)
