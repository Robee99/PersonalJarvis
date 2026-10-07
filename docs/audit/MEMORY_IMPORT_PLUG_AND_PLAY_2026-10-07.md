# Memory import: plug and play (2026-10-07)

**Verdict: NOT-SHIP-READY.**

**PC finding (2026-10-07 07:5x):** the first real import of `C:\AI DATA` on the
owner's PC reported "0 new · 25 skipped": the build then read only Markdown and
`.txt`, and that folder holds other formats. Documents and AI chat exports are
now imported (below); the re-run on the PC is pending.
 Every link of the chain is implemented and tested
in code and on a live Jarvis server, and real Hermes Agent code was shown to
recall an imported note. The acceptance run on the owner's Windows PC
(`C:\AI DATA` and the Obsidian vault) has not happened yet, and that is a
critical link.

Branch `jarvis/local-qwen-build` on `Robee99/PersonalJarvis`.

## The chain

```text
Memory Orb → Import Data → folder or vault → copy into the wiki vault
→ FTS index (same database the wiki already uses) → Memory Orb graph
→ Jarvis recall (wiki-recall) → Hermes, through Jarvis's own MCP surface
```

Nothing new was added for storage: imported pages are ordinary Markdown pages
in the existing wiki vault under `imports/<folder-name>-<hash>/`, indexed by
the existing `jarvis/memory/wiki/fts_index.py`, shown by the existing orb
graph and found by the existing `VaultSearch`. No new database, vector store
or ingestion framework.

## Status per link

| Link | Status | Evidence |
|---|---|---|
| Import Data button and dialog on the Memory Orb (path, Browse, Import, Cancel, progress) | IMPLEMENTED, TESTED | `jarvis/ui/web/frontend/src/views/MemoryOrbImport.tsx`; 3 vitest tests; live Playwright run, screenshot `jarvis-handoff/live-test/import/reimport_ui.png` |
| Import job (one at a time, progress, cancel, retry) | IMPLEMENTED, TESTED | `jarvis/memory/wiki/importer.py`; `tests/unit/memory/wiki/test_importer.py` (9 tests) |
| Routes `POST /api/wiki/import`, `GET /api/wiki/import/{id}`, `POST /api/wiki/import/{id}/cancel`, `POST /api/wiki/import/pick-folder` | IMPLEMENTED, TESTED | `jarvis/ui/web/wiki_routes.py`; route test; live curl run |
| Obsidian vault detection, structure, frontmatter and wikilinks kept | IMPLEMENTED, TESTED | byte-for-byte copy test |
| Documents: PDF, Word, PowerPoint, Excel, OpenDocument, EPUB, RTF, HTML, CSV, JSON, code | IMPLEMENTED, TESTED | the repo's existing `jarvis/documents/extract.py`; one page per file (`report.pdf` → `report.pdf.md`); test with DOCX, HTML, CSV, JSON |
| AI chat exports: ChatGPT and Claude `conversations.json`, chat datasets (`.jsonl` with `messages` or prompt/response) | IMPLEMENTED, TESTED | `jarvis/memory/wiki/import_formats.py`; one page per conversation (datasets: 25 records per page); recall test finds a fact from inside a conversation |
| Index without a manual reindex | IMPLEMENTED, TESTED | `fts_index.upsert_pages`; marker searchable right after import |
| Orb shows the pages | IMPLEMENTED, TESTED | route test reads `/api/wiki/graph`; orb refreshes when the job ends |
| Jarvis recall of the marker | IMPLEMENTED, TESTED | live MCP call `wiki-recall` returned the marker page with its path |
| Hermes recall of the marker | IMPLEMENTED, TESTED (code path), BLOCKED (model turn) | see "Hermes" below |
| Windows acceptance on `C:\AI DATA` and the Obsidian vault | BLOCKED | needs the PC run (see "Still to do") |

## Edge cases

| Case | Behaviour | Test |
|---|---|---|
| Re-import, nothing changed | 0 new, 0 updated, all unchanged; no duplicate pages | unit + live UI ("Done: 1 new · 0 updated · 3 unchanged · 2 skipped" after one note was added) |
| Edited source file | that page updated, searchable with the new text | unit |
| Cancel | stops after the current file; pages already written stay and are indexed | unit |
| Retry after cancel | finishes the rest, no duplicates | unit |
| No text (pictures, recordings, archives, old `.doc/.xls/.ppt`) | skipped and counted per extension with the reason, shown in the panel ("Skipped 20 × .png: picture (no text)") | unit + vitest |
| Binary content, over 2 MB, empty | skipped with that reason | unit |
| Malformed frontmatter | still imported as text | unit |
| Markdown note that looks like it holds a key or token | skipped, never copied; reason shown, content never logged | unit + live (`leaked.md`) |
| Key or token inside a converted document or conversation | masked with `redact_secrets` (`<redacted:openai_key>`); the page is skipped if the guard still finds one | unit |
| Symlinks | not followed (folders or files) | unit (Linux) |
| Importing the vault into itself | refused | unit + live |
| Path traversal in names (`..`, `:`, `?`) | sanitised; target always stays under `imports/` | unit |
| Missing path | refused with a plain message | route test |
| Hidden folders, `.obsidian`, `attachments`, `_archive`, `node_modules` | skipped | unit |

Imported content is only ever read as text. Nothing in it is executed, and the
existing secret guard (`jarvis/memory/wiki/secret_guard.py`) is applied before
any file is written. Logs and the job report carry relative paths and counts,
never file contents.

## Hermes

`jarvis system free-voice` now mounts Jarvis's own MCP surface in Hermes as
`mcp_servers.jarvis`, limited to `wiki-recall` and `wiki-list`. The control
key goes into Hermes's `.env` as `JARVIS_CONTROL_KEY`; `config.yaml` only holds
`${JARVIS_CONTROL_KEY}`.

Checked in the cloud container against Hermes Agent v0.21.5 (source commit
33c9b1d7) installed from source:

1. `connect_hermes_memory` wrote the entry with the real `hermes config set`;
   the key was in `.env` and not in `config.yaml`.
2. `hermes mcp test jarvis` connected (1.5 s).
3. Hermes's own discovery registered exactly `mcp__jarvis__wiki_recall` and
   `mcp__jarvis__wiki_list`, status connected.
4. Hermes's tool registry called `mcp__jarvis__wiki_recall` with
   `JARVIS_IMPORT_TEST_2026_10_07_A` and got the imported page back with its
   path `imports/ai-data-46f5de/Marker Note.md`.

Found and fixed on the way: Hermes was first pointed at `/api/control/mcp`,
which answers 405 to a POST; the entry now uses `/api/control/mcp/`
(commit a4f1dd7a).

What this does not prove: a Hermes model deciding to call the tool for "what
is the import marker?". No free model is reachable from the container, so that
turn is part of the PC run.

## Still to do (PC run)

1. Install the installer built from a4f1dd7a or later.
2. Run `jarvis system free-voice` and restart the Hermes gateway.
3. Import `C:\AI DATA` and the Obsidian vault through Import Data; record
   discovered, imported, skipped (with reasons) and time taken.
4. Ask Jarvis by voice and by chat: "What is JARVIS_IMPORT_TEST_2026_10_07_A?"
   with a marker note placed in `C:\AI DATA`; confirm Hermes called
   `wiki_recall` (Hermes tool event) and the answer names the source page.
5. Edit the marker note, re-import, ask again.
6. Start a large import and press Cancel; import again.

## Known limits

* Imports are copies. An edit in the original folder needs a re-import (it is
  fast: unchanged files are skipped). Edits made to the imported pages inside
  the Jarvis vault are picked up by the existing watcher.
* Pictures and recordings are counted, not described; scanned PDFs without a
  text layer give no text.
* Connecting an existing Obsidian vault in place, without copying, is not
  built.
