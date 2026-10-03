# PersonalJarvis implementation map (Phase 0 evidence)

Branch `jarvis/local-qwen-build` of `Robee99/PersonalJarvis`, inspected
2026-10-03. Every row cites the file and symbol that was read. "Target device"
facts come from read-only checks on the owner's Windows 11 laptop (Lenovo LOQ
15APH8, Ryzen 7 7840HS, RTX 4060 8 GB, 16 GB RAM). Line numbers drift; the
symbol names are the stable reference.

Companion: [implementation status](personaljarvis-implementation-status.md).

## Brain

| What | Where | Notes |
| --- | --- | --- |
| Orchestration boundary | `jarvis/brain/manager.py` `BrainManager.generate` / `generate_stream` | Single entry for every turn; owns chain building, fallback, honesty guards, side-effect recording. Preserved; all new routing lives inside it. |
| Intent level | `jarvis/brain/intent_router.py` `classify` -> `RoutingDecision(level, reason)` | Levels `fast`, `deep`, `code`. The routing policy consumes this level as its complexity signal. |
| Built-in chain | `BrainManager._build_fallback_chain` | Without a policy: primary, then a fixed cross-provider order (`cross_order`), plus override chains (`_override_chain`). |
| Tool/vision capability gates | `_brain_can_call_tools`, `_provider_advertises_vision`, `_first_tool_capable_provider`, `_lead_vision_chain`, `_hoist_tool_model` | Capability-driven (AP-21). Vision is self-declared per provider; OpenRouter treats an unknown model as vision-capable. |
| Provider registry | entry points under `jarvis/plugins/brain/` (`local_openai.py`, `openrouter.py`, `ollama.py`, ...) | `local-openai` speaks OpenAI `/v1/chat/completions` to a configured root (`/v1` appended), declares tools on, vision by model name. One base URL per provider. |
| Turn dispatch and tool loop | `jarvis/brain/dispatcher.py` `BrainDispatcher.dispatch`, `jarvis/brain/tool_use_loop.py` | Tools run through `ToolExecutor.execute`; `StreamingAggregate.executed_tool_names` records tools that really ran. |
| All-providers-down reply | `BrainManager._provider_down_reply` | Localised, provider-agnostic apology. |
| Routing policy (new) | `jarvis/brain/route_policy.py` `decide_route`; `BrainManager._policy_chain`, `_apply_route_deny`, `_publish_route` | Opt-in `[brain.route_policy]`; see status doc. |
| Paperclip delegation (new) | `jarvis/brain/paperclip_delegation.py` `PaperclipDelegate`; `BrainManager._escalate_to_delegate` | Only Claude path when the policy is on. |

## Voice

| What | Where | Notes |
| --- | --- | --- |
| Pipeline and lifecycle owner | `jarvis/speech/pipeline.py` `SpeechPipeline` | Wake, VAD, STT, brain call, sentence streaming, TTS, playback, barge-in. Kept as the voice stack; no new framework. |
| Streaming answer -> TTS | `_brain_streaming` (producer, `sentence_channels` bounded by look-ahead, `_play_sentences`) | Producer teardown deadlock fixed in `5e077c44`. |
| Stall / ceiling guards | `_run_brain_with_stall_guard` (30 s stall, 90 s ceiling), `_speak_brain_timeout(site=)`, `_handle_silent_brain_turn` | Completion path covered in `5e077c44`. |
| Barge-in | local VAD barge detectors, output cancel, brain-task cancel in `pipeline.py`; `jarvis/speech/interrupt_intent.py`, `echo_guard.py` | Cancelling the brain task raises `CancelledError` into an in-flight delegation, which cancels the Paperclip issue (tested). |
| TTS failure surfacing | `ErrorOccurred(layer="speech.tts", error_type="tts_no_audio")` from `_brain_streaming`; UI toast in `useWebSocket.ts` | Added in `5e077c44` / `a6d5d7eb`. |
| Mic diagnostics | `jarvis/speech/diagnose.py` `classify_mic_level`; `jarvis/diagnostics/doctor.py` `check_microphone` | Added in `bb0f9a19`. Target device: the built-in Realtek array delivers digital silence (-90 dBFS MME, -123 dBFS WASAPI); unresolved. |

## Models (target device facts, no secrets)

| Model | Reachable free route | Protocol / capabilities | Status |
| --- | --- | --- | --- |
| Step 3.7 Flash | Nous inference API through Hermes OAuth (`stepfun/step-3.7-flash:free`). Exposed locally by the owner's gateway script `C:\paperclip\tools\nous-free-proxy.mjs` on `127.0.0.1:11436`: `GET /healthz`, `GET /v1/models`, `POST /v1/chat/completions`, SSE pass-through, `max_tokens` capped at 4096, 4 MB request cap, 300 s upstream timeout, free-model allowlist, no client auth (loopback only). | OpenAI chat-completions; tool calling through this route not yet verified live. HTTP 429 fair-share limits observed today. | Gateway not running (scheduled task "at logon", last run exited with code 1). |
| Step 3.7 Flash on OpenRouter | `stepfun/step-3.7-flash` | Tools, text+image+video, 262k ctx | Paid only ($0.20 / $1.15 per M tokens): excluded by the owner's free-only rule. |
| Qwen 3.6 35B | `qwen/qwen3.6-35b-a3b` on OpenRouter | Tools, text+image+video, 262k ctx | Paid only ($0.15 / $1.00 per M): excluded. No local GGUF anywhere on the PC; no server on 11435. Hermes ships a CUDA 13 `llama-server`. |
| Installed Jarvis brain | `%LOCALAPPDATA%\Jarvis\jarvis.toml`: primary/fallback/router `gemini` | Gemini key set (free tier) | `local-openai` points at `127.0.0.1:8080` (nothing listening). |

## Paperclip

| What | Evidence |
| --- | --- |
| Instance | `http://127.0.0.1:3100/api`, `local_trusted` (no auth header needed on the PC). Company "CALI CARTEL", issue prefix `JAR`. |
| Agents | `claude` (claude_local, idle), `dan` / `pi-free` / `opencode-free` (hermes_local, step-3.7-flash free), `hermes` (error state), `gemini`, plus paused ones. |
| Create | `POST /api/companies/:companyId/issues`; `@paperclipai/shared` `createIssueBaseSchema` (title, description, status, assigneeAgentId, ...) extended with `createIssueDuplicateGuardSchema.idempotencyKey` ("idempotency keys always replay their original issue") in 2026.916.1. The base object is not `.strict()`, so an older server ignores the key. |
| Read / result | `GET /api/issues/:id` (status), `GET /api/issues/:id/comments` (agent reply: `authorAgentId` / `authorType`). Also `/runs`, `/api/heartbeat-runs/:runId`. |
| Cancel | `PATCH /api/issues/:id` with `status: "cancelled"`. Statuses: backlog, todo, in_progress, in_review, done, blocked, cancelled. |
| Existing integration before this work | MCP marketplace connector only (`TokenStore().load("paperclip")` with `extra.instance_url`); no Brain delegation client. |

## Tools and safety

| What | Where | Notes |
| --- | --- | --- |
| Single execution chokepoint | `jarvis/safety/tool_executor.py` `ToolExecutor.execute` | Risk tiers safe/monitor/ask/block (`jarvis/safety/risk_tier.py`), approval workflow, voice-confirm two-turn flow, plan-mode read-only gate (`jarvis/core/tool_read_only.py` `allows_read`). |
| Events | `ActionProposed`, `ActionApprovalRequired`, `ActionDenied`, `ActionExecuted` in `jarvis/core/events.py` | `ActionExecuted.output_preview` redacted via `safe_preview`; `ActionProposed.args` and `ActionExecuted.error` are published unredacted. |
| Cancellation and deadlines | `CancelToken` checked before evaluate and before execute; turn-level no-progress deadline in `ToolUseLoop` (`deadline_s`) | No per-tool deadline in the executor; no in-flight cancel of a running tool. |
| Schema validation | Not in the executor | Arguments reach `tool.execute` as the model produced them. |
| Idempotency | None for tools | New: per-attempt side-effect ledger (`recording_side_effects`) prevents provider fallback from replaying an action. |
| Mission workers | `jarvis/missions/init.py` (`ClaudeDirectWorker` default) | Not covered by the routing deny list; a mission can still run on a direct Claude worker. |

## Vision and OCR

| What | Where | Notes |
| --- | --- | --- |
| Screen capture to the Brain | `jarvis/screen_context/service.py`, `turn.py` (`<SCREEN_EVIDENCE>` block), `targeting.py`, `redaction.py` | Accessibility text first, OCR only when enabled and the text is sparse; secrets burned out of pixels. Master switch `[screen_context].enabled`. |
| OCR | `jarvis/screen_context/uitext.py` `ocr_supplement_with_regions` | Tesseract via optional `pytesseract`; `[screen_context].ocr_enabled` defaults to off. Typed `OCR_UNAVAILABLE`; new `OCR_LOW_CONFIDENCE`. Engine not installed on the target device. |
| Vision models | `BrainManager._lead_vision_chain`, `jarvis/vision/*` | Capability self-declared by providers. |

## Reliability and tests

| What | Where |
| --- | --- |
| Test runner | `scripts/ci/run_tests_parallel.py` (markers exclude slow/live/eval) with ratchet `scripts/ci/ratchet_tests.py` against `scripts/ci/test-baseline-linux.json` |
| Gates | `scripts/ci/check_no_new_german.py`, `check_silent_exception_handlers.py`, `check_async_routes.py`, `check_config_switches_wired.py`, `check_dist_consistency.py`, contract guards in `tests/unit/brain/test_routing.py` |
| CI on the fork | `ci.yml` does not run on the fork's PR; only the manual "Desktop installers" workflow runs. All test evidence below is from local runs in a Linux container. |
| Turn correlation | The voice pipeline passes no `trace_id` into the Brain; the dispatcher mints its own. There is no shared `turn_id` across STT, Brain, tools, Paperclip and TTS. |
