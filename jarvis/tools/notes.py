"""Reading inside the user's files. Private: results only go to the local model."""
from pathlib import Path

from jarvis import notes
from jarvis.tools import tool
from jarvis.tools.apps import inside_roots

READ_CHARS = 6000


@tool("Search inside the user's own files (notes, PDFs, slides, documents, code) for passages "
      "about a question. Returns passages with the file path and page.",
      readonly=True, private=True, untrusted=True, max_chars=7000,
      describe=lambda a: f"search inside your files for '{a.get('query', '')}'",
      query="string: what to look for, in plain words", k="?integer: passages, at most 10")
def search_notes(ctx, query, k=6):
    if not ctx.conn.execute("SELECT 1 FROM docs LIMIT 1").fetchone():
        return "The file content index is still being built; try again in a few minutes.", None
    hits = notes.search(ctx.conn, query, k=max(1, min(int(k), 10)), embed=notes.embed)
    if not hits:
        return f"No passages match '{query}'.", None
    return "\n\n".join(f"[{i}] {h['path']}" + (f" (page {h['page']})" if h["page"] else "")
                       + f"\n{h['text']}" for i, h in enumerate(hits, 1)), None


@tool("Read the text of one of the user's files (PDF, Word, PowerPoint, notebook or text). "
      "Long files come in parts: pages for PDFs and slides, 6000-character parts otherwise.",
      readonly=True, private=True, untrusted=True, max_chars=READ_CHARS + 300,
      describe=lambda a: f"read {Path(str(a.get('path', ''))).name}",
      path="string: full path, e.g. from find_files or search_notes",
      page="?integer: page/slide or part number, starting at 1")
def read_file(ctx, path, page=1):
    p = Path(path).expanduser()
    if not p.is_absolute() or not inside_roots(p, ctx.cfg.file_roots, ctx.cfg.file_exclude):
        raise ValueError("that file is outside the folders I'm allowed to read")
    p = p.resolve()
    if not p.is_file():
        raise ValueError("that file does not exist")
    pages = notes.extract(p) or [(1, "")]
    page = max(1, int(page or 1))
    if pages[0][0] > 0:  # paged document: whole pages from the one asked for
        total, text, last = len(pages), "", page
        for n, t in pages[page - 1:]:
            if text and len(text) + len(t) > READ_CHARS:
                break
            text, last = text + t, n
        head = f"{p.name}, page {page}" + (f"-{last}" if last > page else "") + f" of {total}"
    else:
        body = pages[0][1]
        total = max(1, -(-len(body) // READ_CHARS))
        text = body[(page - 1) * READ_CHARS: page * READ_CHARS]
        head = f"{p.name}, part {page} of {total}"
    if not text.strip():
        return f"{head}: no text (past the end, or a scanned document without a text layer).", None
    return f"{head}\n{text[:READ_CHARS]}", None
