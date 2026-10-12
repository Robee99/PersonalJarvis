# Hermes parity matrix (2026-10-07)

One row per capability: what Hermes Agent has, what Jarvis does with it, how
the two are joined, and how far that was proven. The baseline is in
`HERMES_PARITY_BASELINE_2026-10-07.md`; the verdict is in
`JARVIS_HERMES_PARITY_CERTIFICATION_2026-10-07.md`.

## How to read the status

Hermes is the brain for every turn (`jarvis/plugins/brain/hermes.py`, run
`POST /v1/runs` on session `jarvis-main`). So for most capabilities "parity"
means Jarvis passes the turn to Hermes unchanged and relays what Hermes does,
not that Jarvis has a copy. Jarvis keeps its own code only where it must act
before Hermes (reflexes, the look-only guard) or show something (Tool Armory,
Memory Orb).

| Status | Meaning |
|---|---|
| **PASS** | Implemented, automated test green, live test on the owner's PC passed |
| **CODE-TESTED** | Implemented, automated test green in the cloud container (with real Hermes where noted); the live PC test has not run yet |
| **PASS-THROUGH** | Hermes owns it; Jarvis forwards the turn and relays events. No Jarvis code to test beyond the relay; only the live PC test can prove it |
| **BLOCKED** | Cannot be proven until the named blocker is cleared |
| **FAIL** | Tried and does not work |

No row is PARTIAL: every row whose live test has not run says so in
"Live test" and names the blocker.

Live tests run on the owner's Windows PC through the remote session on that
PC, after the installer built from `ddef44b4` (or later) is installed. None of
them has run on that build yet: the owner closed the session while it built.
That one blocker is written as **PC run** below.

## Matrix

| # | Capability | Hermes | Jarvis equivalent | Bridge | Runtime path | Automated test | Live test | Status | Evidence | Blocker |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Skills | 58 bundled + 152 optional SKILL.md in the install; hub/local/external skills in `HERMES_HOME/skills` | Jarvis's own 30 native skills stay; Hermes's skills are counted and shown | `GET /api/hermes/inventory` reads `HERMES_HOME` and the install dir | Tool Armory → Hermes Agent group → Skills / Skill catalog | `tests/unit/brain/test_hermes_inventory.py` (15), `ArmoryHermes.test.tsx` (4); real Hermes source: 152 + 58 found | Armory shows Hermes skills, not "30" only | CODE-TESTED | commit `85f928c3` | PC run |
| 2 | MCP servers | `mcp_servers.*` in `config.yaml`; 65 optional MCP manifests in the install | Jarvis `mcp.json` (3 servers) kept; Hermes's servers listed next to it | same inventory route; catalog from `optional-mcps/` | Tool Armory → MCP servers + MCP catalog | as row 1 | Armory lists Hermes's servers and the 65-entry catalog | CODE-TESTED | `85f928c3` | PC run |
| 3 | Plugins | `hermes plugins list --json` | Jarvis marketplace apps (1/47) kept | inventory route calls the CLI | Tool Armory → Plugins | as row 1; real Hermes: 52 enabled of 58 | Armory shows the plugin count | CODE-TESTED | `85f928c3` | PC run |
| 4 | Memory (Jarvis wiki in Hermes) | MCP client | Jarvis wiki (Memory Orb, `wiki-recall`) | `mcp_servers.jarvis` → `http://127.0.0.1:<port>/api/control/mcp/`, tools `wiki_recall`, `wiki_list`; key in Hermes `.env` | Hermes turn → `mcp__jarvis__wiki_recall` → Jarvis FTS index | `test_free_voice.py` (15); real Hermes v0.21.5: `hermes mcp test jarvis` connected, registry call returned the marker page | "What is JARVIS_IMPORT_TEST_2026_10_07_A?" by chat and voice, Hermes tool event seen | CODE-TESTED | `a4f1dd7a`; `MEMORY_IMPORT_PLUG_AND_PLAY_2026-10-07.md` | PC run (a free model must choose the tool) |
| 5 | Memory import | — | Memory Orb → Import Data: Markdown, documents, ChatGPT/Claude exports, chat datasets | existing wiki vault + FTS index | importer job → pages under `imports/` → orb | `test_importer.py` (11), `MemoryOrbImport.test.tsx` (3), live Playwright run | re-import `C:\AI DATA` (25 files skipped on the old build) | CODE-TESTED | `eb98ad81` | PC run |
| 6 | Sessions | Server-side session history | Jarvis sends only the new turn | `session_id: jarvis-main` on every run | Hermes keeps history; Jarvis keeps none of its own for the brain | `test_hermes_session.py` | turns 2 and 3 remember turn 1 | PASS-THROUGH | `hermes.py` `SESSION_ID` | PC run |
| 7 | Context size | `compression.threshold_tokens` | — | `jarvis system free-voice` sets it to 40 000 | Hermes compacts the session past the cap | `test_free_voice.py` (context cap test) | reply latency after compaction (was 6–52 s at 99k–207k tokens) | CODE-TESTED | `ddef44b4` | PC run |
| 8 | Delegation | `delegate_task` subagents, async delegation | Paperclip delegate kept for Paperclip jobs | none needed: Hermes calls its own tool inside the run | `tool.started` events relayed | `test_hermes_session.py` (background delegation result comes back and is spoken) | "research X in the background" shows a delegate tool event | PASS-THROUGH | Hermes `tools/delegate_tool*.py` | PC run |
| 9 | Checkpoints / rollback | `checkpoint_manager`, `/rollback` | none | Hermes takes checkpoints before file edits inside the run | — | none on the Jarvis side (no Jarvis code) | edit a scratch file, ask to undo | PASS-THROUGH | Hermes `tools/checkpoint_manager.py` | PC run |
| 10 | Browser | `browser_*` tools (CDP, snapshot, vault) | Jarvis legacy browser tools not used as a substitute | Hermes tools inside the run; acting browser tools are blocked on look-only turns | `tool.started` → relay | `test_hermes_parity.py` (look-only stop on `browser_click`) | open a page and read its title | PASS-THROUGH | Hermes `tools/browser_tool*.py` | PC run |
| 11 | Computer use | `computer_use` tool; input actions need approval | Approval asked by voice/chat and answered once/deny | `approval.request` → Jarvis asks → `POST /v1/runs/{id}/approval` | `_answer_pending` in `hermes.py` | `test_hermes_session.py` (yes / no / unclear / moving on; spoken round trip) | "open Notepad and type hello" asks first | CODE-TESTED | `hermes.py` approval relay | PC run |
| 12 | Computer use, negative | — | "Don't click anything, just look at my screen" must not act | Jarvis gate declines local actions; Hermes run gets observe-only instructions; approvals auto-denied; run stopped on an acting tool | `cu_gate.is_observation_only`, `HermesBrain._relay` | `test_cu_gate.py`, `test_local_action_gate.py`, `test_hermes_parity.py` (370 brain tests pass) | say it with an app open; nothing clicked | CODE-TESTED | `455cc710`, `60560281` | PC run |
| 13 | Vision end to end | image input; non-vision models go through `vision_analyze` | screen/photo attached to the turn | `run_input()` sends `image_url` data-URL parts; `supports_vision = True` | consent still decided by `route_policy` (cloud images off unless allowed) | `test_hermes_parity.py` (image part sent; text-only turn unchanged) | "what's on my screen?" answers from the picture | CODE-TESTED | `60560281` | PC run |
| 14 | Voice loop | text in, text out | wake word, STT, TTS (free voice) | Jarvis voice → Hermes run → streamed deltas → TTS | `BrainManager` → `HermesBrain` | existing voice tests; `test_free_voice.py` | a spoken question gets a spoken answer | CODE-TESTED | `free_voice.py` | PC run |
| 15 | Fast reflex | — | navigation reflex (kept) and brightness reflex (new) run before Hermes; negations and questions never act | reflex answers alone; otherwise the turn goes to Hermes | `BrainManager` fast paths | `test_brightness.py` (25), `test_local_action_gate.py` ("don't open Spotify" → no plan) | "don't open Spotify" opens nothing; "is it bright?" changes nothing | CODE-TESTED | `455cc710`, `60560281` | PC run |
| 16 | Honest brightness | — | set brightness, read it back, report the read value | WMI via PowerShell | `apply_brightness` (tolerance 3, settle 0.4 s) | `test_brightness.py` (read-back mismatch reported, not claimed) | "set brightness to 40" on the laptop panel; external monitors report "not supported" | CODE-TESTED | `60560281` | PC run |
| 17 | Dynamic Tool Armory | live toolsets via the API server | no hardcoded 30 / 3 / 1: Hermes counts come from discovery | inventory route (cached, each part reports its own error) | Tool Armory | `test_hermes_inventory.py`, `ArmoryHermes.test.tsx` | Armory numbers match `hermes skills list` / `hermes mcp list` | CODE-TESTED | `85f928c3`, bundle `45071668` | PC run |
| 18 | Free models only | `auxiliary.free_only` (default false) | free models only, paid optional | free-voice sets `auxiliary.free_only true`; fallbacks are `:free` models | Hermes auxiliary chain | `test_free_voice.py` (free-only test) | Hermes log shows only `:free` models | CODE-TESTED | `fc6dddfd` | PC run |
| 19 | Latency | — | — | context cap (row 7) | — | — | time from end of speech to first word, five turns | BLOCKED | PC log 2026-10-07: 6–52 s per reply | PC run |
| 20 | Failure handling | run ends with a category | Jarvis says Hermes did not finish and why; cut-off runs are stopped with `/v1/runs/{id}/stop` | relay error categories; no paid fallback | `HermesBrain._relay`, `_close` | `test_hermes_hardening.py` (failed, partial or disconnected runs never count as success); `test_hermes_session.py` (cut-off turn stops the run) | stop the Hermes gateway mid-turn: Jarvis says so plainly | CODE-TESTED | `hermes.py` | PC run |
| 21 | Security | approvals, secret redaction | wiki secret guard; Jarvis MCP limited to `wiki_recall`/`wiki_list`; key only in Hermes `.env`; imported content never executed | `tools.include` on the MCP entry | — | `test_free_voice.py` (key not in config), `test_importer.py` (keys masked or skipped) | `config.yaml` on the PC holds `${JARVIS_CONTROL_KEY}` only | CODE-TESTED | `a4f1dd7a`, `eb98ad81` | PC run |

## Known Hermes defect found on the way

`GET /v1/skills` on the Hermes API server answers 500 in v0.21.5 (it calls
`_find_all_skills(include_editorial=True)`). Jarvis does not use that route:
the inventory reads the skill folders directly.
