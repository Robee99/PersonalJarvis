# Hermes parity baseline (2026-10-07)

The state recorded before the parity work started, so later results can be
compared with it. Nothing in this file was changed to make the baseline look
better.

## Code

| Item | Value |
|---|---|
| Repository | `Robee99/PersonalJarvis` |
| Branch | `jarvis/local-qwen-build` |
| Baseline commit | `0faa4dc1` (memory import pushed; no parity changes yet) |
| Python (cloud test container) | 3.11.15 |
| Node (frontend build) | 22.22.0 |

## Hermes Agent

| Item | Value | Source |
|---|---|---|
| Version on the owner's PC | v0.21.x desktop install, `%LOCALAPPDATA%\hermes` | PC session, 2026-10-05 |
| Version used for container checks | v0.21.5+6947.g33c9b1d (2026.9.24), installed from source commit 33c9b1d7 | `hermes --version` |
| How Jarvis reaches it | API server on 127.0.0.1:8642, `POST /v1/runs`, session `jarvis-main`, key read in place from Hermes's `.env` | `jarvis/plugins/brain/hermes.py` |
| Model on the PC | Hermes's own pick; observed `nous/meituan/longcat-2.5-preview:free` | Jarvis log on the PC, 2026-10-07 |
| Local providers configured in Hermes | `local-qwen` (127.0.0.1:11437), `local-gemma` (127.0.0.1:11438) | PC session, 2026-10-05 |
| Fallbacks | free models only (longcat-2.5-preview:free, laguna-s-2.1:free) | PC session, 2026-10-03 |

## What the Tool Armory showed (owner's screenshot, 13:19 PC time)

| Card | Value | Where the number came from |
|---|---|---|
| Apps | 1 / 47 connected | `/api/marketplace/plugins` (Jarvis catalog; Paperclip connected) |
| MCP servers | 3 / 3 running | `/api/mcps` (Jarvis's own `mcp.json`) |
| Skills | 30 ready to use | `/api/skills` (Jarvis's native skill registry) |

None of the three counted anything Hermes has: Hermes's skills, MCP servers,
plugins and toolsets were invisible in Jarvis.

## Gaps found by reading the code at the baseline

| Area | Finding | Evidence |
|---|---|---|
| Vision | `HermesBrain.supports_vision = False`; the run's `input` was text only, so images were dropped | `jarvis/plugins/brain/hermes.py` (baseline) |
| Look-only | "Don't click anything, just look at my screen" allowed computer use and produced a COMPUTER_USE plan | `jarvis/brain/cu_gate.py`, `jarvis/brain/local_action_gate.py` |
| Negation | "don't open Spotify" and "do not open chrome" produced a DIRECT `open_app` plan | `local_action_gate.match_local_action` |
| Brightness | no brightness reflex and no read-back anywhere | repo-wide search |
| Volume, lock, media reflex | none in Jarvis; with Hermes as the brain every Jarvis reflex except navigation stands down | `BrainManager._brain_orchestrates_tools` |
| Paid fallback | Hermes's auxiliary auto-chain may use a paid OpenRouter model when a key is present (`auxiliary.free_only` defaults to false) | Hermes `hermes_cli/config_defaults.py` |
| Memory | Hermes could not reach Jarvis's wiki | no `mcp_servers.jarvis` entry |
| Hermes API `/v1/skills` | raises a TypeError in this Hermes build (calls `_find_all_skills(include_editorial=True)`) and answers 500 | Hermes `gateway/platforms/api_server.py` |

## Tests at the baseline

The unit and integration suites of the whole repository do not finish inside
the container's time budget when run together. The comparison used for this
work runs the suites around the changed code (`tests/unit/brain`,
`tests/unit/cli_ctl`, `tests/unit/platform`, `tests/unit/ui/web`,
`tests/unit/plugins`, `tests/unit/memory`) on the baseline commit and on the
final commit in separate checkouts, and compares the failing test ids. Its
result is recorded in the certification report.

Already failing at the baseline, in the container: `test_manager_pointer` (2)
and `test_manager_screen_context` (2), which expect a screen capture the
container cannot make.
