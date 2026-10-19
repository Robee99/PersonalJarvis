# Voice and text repair — 2026-10-20

## Application identity and scope

- Source base: `d91d527afa8de1255a795fae8c1f2b70d86ec4d9`,
  `origin/jarvis/local-qwen-build`, version 3.1.1.
- Repair branch: `codex/voice-text-repair-20261020` in an isolated worktree.
- Installed application: `%LOCALAPPDATA%/Programs/Personal Jarvis/PersonalJarvis.exe`.
- All five installed modules were independently compared with compiled base
  source under Python 3.12; their code matched before preparing the repair.
- No frontend, model weights, context-window setting, MCP configuration,
  credentials, conversation database, or other working tree was edited.

## Confirmed faults and changes

The desktop log recorded a local voice turn being declared stalled after
30 seconds, although the model continued working. The OpenAI-compatible
adapter discarded prompt-progress events, private reasoning, and unfinished
function arguments. Both the tool loop and the tool-free dispatcher also
lost progress when a routing round suppressed its text consumer.

- `jarvis/plugins/brain/_openai_base.py`: emit an empty progress delta for
  increasing prompt-processing counts, nonempty reasoning, and partial tool
  arguments. Private reasoning is not exposed as answer text; partial calls
  are not executed. Empty transport frames do not fabricate progress.
- `jarvis/plugins/brain/local_openai.py`: request `return_progress` only when
  the selected model's server declares `owned_by=llamacpp`. Discovery is
  bounded to two seconds, cached per adapter, and optional on failure.
  Heartbeat-only output does not disable existing grammar-recovery retries.
- `jarvis/brain/tool_use_loop.py` and `jarvis/brain/dispatcher.py`: deliver
  stream progress even when the routing round suppresses spoken text.
- `jarvis/agent_chat/runner_brain.py`: an empty final answer is reported as an
  error instead of falsely completing an empty chat turn successfully.

The existing silence timeout, hard ceiling, cancellation, permission checks,
and provider-fallback policy remain in force. This repair does not guarantee
that every possible provider, microphone, or audio device works.

## Verification

Regression tests first failed on discarded reasoning, tool fragments, prompt
progress, both silent routing paths, and blank chat success. The repaired
paths pass their regressions.

- Combined affected provider, chat, watchdog, routing, output-filter,
  hangup-reason, and language tests: **919 passed, 1 failed** in 91.76 s.
- The failure is `test_router_tools_is_pure_dispatcher_set`: its expected
  set lacks the existing `cognee-source` and `kept-archive-search` entries.
  Reproduced the identical failure on an isolated export of exact base
  `d91d527afa`. No expected set or baseline was weakened to hide it.
- Additional real-SDK/SSE integration: **1 passed**. The actual OpenAI SDK,
  local adapter, dispatcher, and speech watchdog process simulated HTTP
  prompt-progress events across a 480 ms prefill with a 200 ms stall window.
  This verifies transport and watchdog integration, not physical microphone
  capture or a real model's generated answer.
- Ruff passes on other changed source/test files. `tool_use_loop.py` has the
  same six legacy findings as the exact base; no new finding identities.
- `git diff --check` passes. Test reports are outside the checkout; the user's
  `report.xml` and wiki data were not changed.
- Independent reviewer found no issues in the five source changes.

## Prepared Windows artifact

An incremental frozen build recompiles exactly the five changed Python
modules with Python 3.12.13. It retains the original PyInstaller bootloader,
runtime hooks, dependency metadata, and **13,784 other module blobs byte for
byte**. Native libraries and the current HUD bundle are untouched. Both
executables report `jarvis 3.1.1`.

An isolated frozen launch using temporary configuration and data returned:

- `/api/health`: 200, version 3.1.1, instance `dev`.
- `/api/brain/status`: 200, `local-openai`, `qwen3-vl-8b`.
- `/api/voice/status` and `/api/voice/state`: 200. Voice was deliberately
  disabled for this headless check; these are not live audio acceptance.
- Health became available after 4.76 seconds. The isolated test process was
  then explicitly terminated; a graceful production shutdown was not tested.

Final prepared executable SHA-256 values:

- GUI: `0fb4b9e4db84dea9f63f737048c9d1fc577beb8b8787799342d86cbfc3efc67d`
- CLI: `71301cb6122d6960609a5d32567bc626b17c6177aeb6d99b4faf0926667feb70`

This is a local incremental repair, not a rebuilt/published installer or a
new release.

## Activation and rollback

At staging time the running app had an active chat. Its restart route
explicitly requires a normal interactive desktop action. The user closed
JARVIS normally; no restart approval was impersonated and no active user
process was terminated by the repair.

Both original executables are backed up under:

`%LOCALAPPDATA%/Jarvis/backups/voice-text-20261020/`

A one-time staged installer waits for the existing app to close normally. It
verifies the original, backup, and repaired executable hashes, replaces only
the two executables atomically, then reopens the same installed app and records
its health result. Unexpected file changes or another active app instance
abort the update. The staging directory is:

`C:/Users/rober/Documents/Codex/2026-10-12/referenced-chatgpt-conversation-this-is-an/outputs/voice-text-repair-20261020/`

Its `apply-status.json` records whether activation is waiting, applied and
healthy, or failed. A prepared artifact alone was not considered live verification. The applied
checks below supersede that staging state; physical microphone/speaker
conversation acceptance remains unverified.

Rollback: after closing JARVIS normally, restore `PersonalJarvis.exe` and
`jarvis.exe` from the backup directory to the existing install directory and
open the normal shortcut. Configuration and all user data remain in place.


## Applied to the actual Windows application

The user closed the app normally. The one-time updater applied the reviewed
executables and reopened the same installed path, process 13772.

- Installed GUI and CLI hashes match the prepared repair exactly.
- The original configuration TOML and HUD `index.html` hashes are unchanged.
- `/api/health` returns 200, version 3.1.1, default instance.
- `/api/brain/status` returns `local-openai` / `qwen3-vl-8b`.
- `/api/voice/status` returns `ready=true`, `detail=listening`.
- A single authenticated, read-only-plan chat selected local Qwen explicitly
  and returned the genuine answer `VOICE_TEXT_REPAIR_OK`, status `done`,
  11,006 input tokens and 7 output tokens. The round trip took 60.81 seconds
  while other local model work was running; no speedup is claimed. The
  original new-chat provider selection was restored immediately after
  creating the clearly labeled verification chat. No paid fallback was
  permitted by the per-chat provider override.
- A real offline Piper preview through the app returned HTTP 200 and
  316,008 bytes of non-silent WAV audio: 6.58 seconds, 24 kHz, mono, 16-bit,
  generated in 4.33 seconds. It was saved as diagnostic audio, not played
  through the user's speakers.
- An unauthenticated write probe was refused with 403. The successful test
  used the existing control credential without printing or modifying it;
  no browser-presence or restart protection was bypassed.

Deployment and real text/TTS generation are verified. A physical spoken
exchange, microphone capture, speaker playback, and acoustic interruption
are not verified by these checks. The watchdog regression is supported by
real SDK/SSE integration and focused timing tests, not a claim of having
heard the user speak.
