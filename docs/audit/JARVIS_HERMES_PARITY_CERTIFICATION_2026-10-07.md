# Jarvis × Hermes parity certification (2026-10-07)

**Status: NOT-SHIP-READY**

Every capability in `HERMES_PARITY_MATRIX_2026-10-07.md` is implemented and
has a green automated test, but none of the live Windows tests has run on a
build that carries the parity work. A capability that has only been shown in
the cloud container is not certified, so the status cannot be SHIP-READY yet.

## What is certified in code

| Area | Result | Commit |
|---|---|---|
| Jarvis wiki reachable from Hermes (`mcp_servers.jarvis`, trailing-slash URL) | real Hermes v0.21.5 connected and recalled the marker page | `a4f1dd7a` |
| Memory import of documents and AI chat exports | 11 importer tests, 3 UI tests, live UI run in the container | `eb98ad81` |
| Negations, questions and look-only turns never act | 370 brain tests pass | `455cc710` |
| Images reach Hermes; look-only turns deny approvals and stop acting tools; brightness with read-back | 6 parity tests, 25 brightness tests | `60560281` |
| Hermes side models stay free (`auxiliary.free_only`) | free-voice test | `fc6dddfd` |
| Tool Armory shows Hermes's skills, MCP servers, plugins, toolsets and catalog | 15 backend tests, 4 UI tests; real Hermes source counted 65 MCPs and 152 + 58 skills | `85f928c3`, bundle `45071668` |
| Hermes session capped at 40 000 tokens before compaction | free-voice test | `ddef44b4` |

## Regression check

The suites around the changed code (`tests/unit/brain`, `cli_ctl`, `platform`,
`ui/web`, `plugins`, `memory`) are run on the baseline and on the parity head
in separate checkouts and their failing test ids compared. Result: _pending,
filled in when both runs finish._

## What blocks SHIP-READY

1. **PC run of the 17 live tests** on the installer built from `3581be18` or
   later (the build from `ddef44b4` was superseded before it ran). The owner's
   PC reported, on the older build `a4f1dd7a`: the Armory still showed
   1 / 47, 3 / 3 and 30; `C:\AI DATA` imported nothing (25 skipped); replies
   took 6–52 s. Each of these has a fix in this branch; none is confirmed on
   the PC yet.
2. **Latency** has no fix proven on real hardware. The context cap is the
   expected fix for the slow replies; it is unmeasured.
3. **The wiki vault on a native install lives inside the program folder**
   (`%LOCALAPPDATA%\Programs\Personal Jarvis\_internal\wiki\obsidian-vault`),
   while settings and data were moved to `%LOCALAPPDATA%\Jarvis`. Inno Setup
   leaves files it did not install in place on upgrade and uninstall, so the
   pages are not deleted, but the uninstaller's message says memory stays in
   `%LOCALAPPDATA%\Jarvis`, which is not true for the vault. Moving the vault
   changes where the owner's memory and Obsidian point, so it waits for the
   owner's decision rather than happening silently.

## Live tests to run on the PC

| # | Test | Pass when |
|---|---|---|
| 1 | Tool Armory | Hermes group shows its skill, MCP, plugin and catalog counts |
| 2 | Import `C:\AI DATA` | documents and chat exports imported; skipped files listed with reasons |
| 3 | Import the Obsidian vault | notes and folders kept; re-import shows all unchanged |
| 4 | Marker recall by chat | answer names `JARVIS_IMPORT_TEST_2026_10_07_A` and its page; Hermes tool event `wiki_recall` |
| 5 | Marker recall by voice | same, spoken |
| 6 | Session memory | turn 3 remembers a fact from turn 1 |
| 7 | Reply latency | five turns timed after compaction |
| 8 | Free models only | Hermes log names only `:free` models |
| 9 | "Don't open Spotify" | nothing opens |
| 10 | "Is my screen bright?" | nothing changes |
| 11 | "Set brightness to 40" | read-back value reported; external monitor says not supported |
| 12 | "Don't click anything, just look at my screen" | description only; no click, no approval |
| 13 | "What's on my screen?" | answer matches the screen (cloud images only with consent) |
| 14 | Computer use with approval | Jarvis asks first; "no" stops it |
| 15 | Browser | Hermes opens a page and reads its title |
| 16 | Delegation | a background task comes back and is spoken |
| 17 | Hermes stopped mid-turn | Jarvis says plainly that Hermes did not finish; no paid fallback |
