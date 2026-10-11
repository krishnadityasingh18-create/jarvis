from jarvis import db
from jarvis.tools import tool


@tool("Save a fact the user asked you to remember. It stays across restarts.",
      exports=True,   # memories go into every prompt, cloud models included
      text="string: the fact, in one short sentence")
def remember(ctx, text):
    mid = db.add_memory(ctx.conn, text)
    return f"Remembered as [{mid}].", {"kind": "delete_memory", "id": mid}


@tool("Forget a remembered fact by its [id].", memory_id="integer: the memory's id")
def forget(ctx, memory_id):
    text = db.delete_memory(ctx.conn, int(memory_id))
    if text is None:
        raise ValueError(f"no memory with id {memory_id}")
    return f"Forgot [{memory_id}] {text}", {"kind": "restore_memory", "id": int(memory_id),
                                            "text": text}


def memory_block(conn) -> str:
    mems = db.memories(conn)
    if not mems:
        return ""
    return "Things the user asked you to remember:\n" + "\n".join(
        f"[{m['id']}] {m['text']}" for m in mems)
