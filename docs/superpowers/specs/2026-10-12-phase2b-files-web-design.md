# JARVIS Phase 2b: File Contents, Reversible Writes and Web — Design

Status: the user chose full read access plus write access ("B"), with one rule: JARVIS
"cannot permanently do something unless I have told it to override all its permissions".
They then granted full authority for the session (no payments). Everything else here,
including this spec's approval, was decided under that authority; decisions are listed in
section 11.

## 1. Goal

Ask questions about your own notes, PDFs, slides and code, answered on this laptop by the
local model. Create, edit, move and delete files, with every change reversible. Search the
web and read pages, with sources cited.

## 2. Scope

In: a content index (text extraction, chunks, full-text and vector search) over the home
folder; `search_notes`, `read_file`; `create_file`, `write_file`, `edit_file`,
`create_folder`, `move_path`, `delete_path` (to a JARVIS trash, never permanent);
`web_search`, `read_page`; a `researcher` agent; privacy routing for file contents; a
"tainted turn" rule against prompt injection.

Out: emptying the trash or any other permanent operation (no tool can do it; see §5);
OCR of scanned PDFs; images; accounts and briefing (Phase 3).

## 3. Content index

- **What:** files under `[files].roots` (home), skipping `[files].exclude` and the new
  `[files].content_exclude` (tool clutter: `My project`, `.claude`, `.vscode`,
  `.platformio`, `.codeium`, `.pio`, `Library`, `build`, `dist`, `.idea`) and the JARVIS
  folder itself.
- **Formats:** `.pdf` (PyMuPDF, per page), `.docx` (python-docx), `.pptx` (python-pptx, per
  slide), `.ipynb` (cell sources), and plain text: `.md .txt .rst .csv .tex .py .c .cpp .h
  .hpp .ino .m .java .js .ts .html .css .sql .yaml .yml .toml .ini .json`. Caps: PDF ≤ 50 MB,
  others ≤ 2 MB, at most 300 pages per document.
- **Chunks:** ~1000 characters with 150 overlap, never across a page/slide boundary; each
  chunk keeps `path` and `page` (1-based; 0 when not paged).
- **Search:** SQLite FTS5 (BM25) top 30 + cosine similarity over `nomic-embed-text`
  vectors (Ollama, 768-d, `search_document:` / `search_query:` prefixes) top 30, merged by
  reciprocal rank fusion (k = 60). Vectors live in SQLite as float32 blobs; search loads
  them into a numpy matrix, reloaded when the chunk count changes.
- **Updates:** the existing indexer thread, after the filename pass, re-extracts files whose
  `(mtime, size)` changed, drops removed files, and embeds missing vectors in batches of 64.
  If Ollama is down, chunks are stored without vectors (full-text still works) and filled in
  on a later pass.

```sql
CREATE TABLE docs (path TEXT PRIMARY KEY, mtime REAL, size INTEGER, chunks INTEGER, error TEXT);
CREATE TABLE chunks (id INTEGER PRIMARY KEY, path TEXT, page INTEGER, text TEXT, vec BLOB);
CREATE INDEX chunks_path ON chunks(path);
CREATE VIRTUAL TABLE chunks_fts USING fts5(text, content='chunks', content_rowid='id');
ALTER TABLE messages ADD COLUMN private INTEGER DEFAULT 0;   -- if missing
```

## 4. Privacy (file contents stay on the laptop)

- `search_notes` and `read_file` are **private** tools. Once one runs in a turn, the turn is
  private: every later model step in that turn uses the `local` route with no fallback.
  Delegating to an agent with `fallback = false` (the `private` agent) also makes the turn
  private.
- The saved assistant row is flagged `private`. When history is sent to any route other than
  `local`, private turns' tool results and assistant text are replaced with
  `[answer about the user's files, kept on this laptop]`.
- `[files].content_private = true` (default). Setting it to false lets cloud models read
  file contents.
- The local model is `jarvis-local`: `qwen3:4b-instruct` with a 12,288-token context,
  created once from the repo's `Modelfile` (Ollama's default context would silently cut
  off passages).

## 5. Writes (reversible)

| Tool | Does | Confirm | Undo |
|---|---|---|---|
| `create_file(path, content)` | new text file; fails if it exists | no | move it to the trash |
| `write_file(path, content)` | replace an existing text file | **yes** | put the old version back |
| `edit_file(path, old, new)` | replace one exact, unique snippet | **yes** | put the old version back |
| `create_folder(path)` | make a folder (and parents) | no | remove it if still empty |
| `move_path(src, dst)` | move or rename; fails if `dst` exists | **yes** | move it back |
| `delete_path(path)` | move a file or folder to the trash | **yes** | restore it |

- **Trash:** `data/trash/<action id>/`, inside the JARVIS data folder. Old versions from
  `write_file`/`edit_file` also go there. Nothing is ever erased by JARVIS; emptying the
  trash is the user's own manual step. This is how "nothing permanent unless I override"
  is met: there is no permanent operation to override in 2b.
- **Where:** inside the roots, not in an excluded folder, not inside the JARVIS install
  folder (no self-modification, no writing scripts it could then run), and never a type
  that can run code (`_runs_code`: our list plus Windows `AssocIsDangerous`). Text content
  only, ≤ 1 MB. Undo refuses if restoring would overwrite something that now exists.

## 6. Web

- `web_search(query, n=5)`: ddgs text search → numbered title, URL, snippet.
- `read_page(url)`: `primp` with Chrome impersonation (plain clients get 403 from Wikipedia
  on this connection), HTML → trafilatura, PDF → PyMuPDF; ≤ 8000 characters. Only `http(s)`;
  refuses `localhost`, loopback, private and link-local addresses (also after DNS).
- **Citations:** persona rule: when web results were used, the on-screen part ends with a
  `Sources:` list of markdown links; URLs are never spoken.
- **Agent:** `[agents.researcher]` route `reasoning`, fallback true, tools
  `["web_search", "read_page"]`.

## 7. Untrusted content (prompt injection)

Web pages and file contents can contain instructions. Their tool results are wrapped as
`<untrusted source="…">…</untrusted>` with a note that instructions inside are data. After
any web or file-content tool runs in a turn, the turn is **tainted**: every tool with a side
effect (anything not read-only) needs confirmation for the rest of the turn, including ones
that normally don't (`open_app`, `create_file`, `remember`, …).

## 8. Errors

| Situation | Behaviour |
|---|---|
| Extraction fails (encrypted PDF, corrupt file) | `docs.error` set, file skipped, retried when it changes |
| Ollama embeddings down | full-text search only; vectors filled later |
| Local model down during a private turn | "I can't reach the local model, sir." — never falls back to cloud |
| `read_page` blocked / not text | error result with the status; the model can try another source |
| ddgs rate-limited | error result; no retry loop |
| Undo target changed | refuse with a reason; nothing overwritten |

## 9. Testing

Unit tests, no network: extraction for each format (generate PDF/DOCX/PPTX in tmp);
chunking; incremental index (change, delete, error); FTS + vector fusion with a fake
embedder; `search_notes`/`read_file` guards; every write tool + undo + trash; path guards
(outside roots, excluded, JARVIS folder, dangerous types); web tools with fake ddgs/primp;
SSRF guard; taint forcing confirmation; private turn switching to local without fallback;
history redaction. Live: index the real home folder, ask about real notes, a web question
with sources, a full write/undo cycle in a scratch folder.

## 10. Demo (ships in README)

1. "What do my physics notes say about [topic]?" → passages found, answered locally,
   sources (file, page) on screen.
2. "Summarise the 18MAB102T May 2019 paper." → reads the PDF, local answer.
3. "Create a file called todo.md on my Desktop with three study tasks." → created; Undo.
4. "Change 'three' to 'four' in todo.md." → confirm → edited; Undo restores.
5. "Delete todo.md." → confirm → it moves to `data/trash`; Undo restores it.
6. "Search the web for the latest ESP32-S3 datasheet revision." → answer with Sources.
7. "Ask the researcher to compare buck and boost converter efficiency." → researched answer
   with sources.

## 11. Decisions made under delegated authority (2026-10-12)

1. Full read covers the whole home folder, but tool clutter is skipped by a
   `content_exclude` list so answers come from real notes.
2. Writes are reversible by design (own trash, backups); no permanent operation exists, so
   the "override" rule needs no switch yet.
3. Overwrites, edits, moves and deletes ask first (the user's standing rule); creating new
   files and folders doesn't.
4. JARVIS never writes inside its own install folder.
5. File contents only reach the local model unless `content_private = false`.
6. `jarvis-local` model variant with a 12k context (fits next to Whisper and Kokoro in 8 GB).
7. `primp` for page fetching; Wikipedia blocks plain clients from this connection.
8. Tainted-turn rule against prompt injection from pages and files.
9. No OCR in 2b (scanned PDFs yield no text and are reported as such).

## 12. Cost

₹0. New dependencies are free (PyMuPDF, python-docx, python-pptx, ddgs, trafilatura,
`nomic-embed-text`). DuckDuckGo search needs no key.
