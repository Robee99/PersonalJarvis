# PersonalJarvis implementation status

Branch `jarvis/local-qwen-build` (fork `Robee99/PersonalJarvis`, draft PR #1).
Last updated 2026-10-03. Evidence for every code reference is in the
[implementation map](personaljarvis-implementation-map.md).

**Verdict: software gates pass; two device gates are blocked.** The final
hardening pass (2026-10-03) and the freeze pass (2026-10-04) are recorded in
[final-hardening-evidence.md](research/final-hardening-evidence.md) and
ADR-0039 to ADR-0041. Voice interruption is HARDWARE-BLOCKED by a Windows
audio setting on the PC, and local visual understanding is RESOURCE-BLOCKED
(no vision projector, not enough free RAM). Nothing here claims measured
barge-in reliability.

## Preserved architecture decisions

- The JARVIS Brain (`BrainManager`) stays the single orchestration boundary.
  Routing is a pure policy function (`jarvis/brain/route_policy.py`) that the
  Brain calls while building each turn's chain; no second router or service.
- The voice stack is unchanged apart from the lifecycle fixes listed below; no
  new voice framework. Hermes Voice Mode, pipecat and livekit-agents were
  evaluated and not adopted (ADR-0039).
- Model choice is configuration- and capability-driven. Provider and model
  names appear only in `[brain.route_policy]`; the policy never compares
  against a provider or model id (AP-21).
- Claude is reached only through Paperclip when the policy is on: deny lists
  remove direct Claude providers from every chain, and escalation creates a
  Paperclip issue instead of opening a model client. Escalation happens only
  on an explicit trigger phrase (ADR-0040).
- Images stay on route targets marked `local` unless the owner sets
  `allow_cloud_vision` (ADR-0040).

## P0 reconciliation

| Audit item | Before this work | Now | Evidence |
| --- | --- | --- | --- |
| Three-stage routing (Step -> Qwen -> Paperclip Claude) | **Missing.** Generic primary + fixed `cross_order` fallback; `claude-api` first in the built-in order. | **Present in code, not live.** Opt-in `[brain.route_policy]` with `fast`, `deep`, `escalation` tiers, deny lists, capability/availability exclusion with reasons, one bounded fallback, never repeats a target, deterministic test override only under `JARVIS_ROUTE_POLICY_TEST_MODE=1`. Not enabled on the PC because the Step and Qwen endpoints are not reachable yet. | `route_policy.decide_route`; `BrainManager._policy_chain`, `_apply_route_deny`; tests `test_route_policy.py` (19), `test_route_policy_manager.py` (6). |
| Paperclip contract | **Missing.** MCP connector only. | **Present in code; live smoke test not run.** `PaperclipDelegate`: resolves company and agent, creates one issue with redacted bounded context, the turn id and an `idempotencyKey`, polls status, reads the agent's last comment, cancels the issue on deadline, cancel token or task cancellation (barge-in), maps every outcome to a typed failure and a user-safe message, per-session escalation budget. Routes and fields checked against Paperclip's own validators and the PC's running instance. | `paperclip_delegation.py`; `BrainManager._escalate_to_delegate`; `test_paperclip_delegation.py` (11), `test_route_policy_manager.py` escalation cases. |
| Qwen/Step endpoint compatibility | **Unknown.** | **Step measured live; Qwen blocked.** Step 3.7 Flash through the owner's Nous gateway: first token 1.9–3.0 s, valid tool call, image read correctly, 4 of 11 requests rate-limited (evidence §7). The gateway still has to be started by hand. Qwen 3.6 35B UD-Q4_K_XL is now on the PC (SHA-256 verified), but a 65k-context run returned empty output with 0.08 GB RAM free; no stable configuration exists yet. | Evidence §7, §8. |
| Target-device release evidence | **Missing.** | **Missing.** The built-in microphone still delivers digital silence on the PC, so no voice run is possible yet. | Map: "Voice", mic row. |
| OCR/vision accuracy gate | **Missing.** OCR passed every word as fact; one `OCR_UNAVAILABLE` code; no fixtures. | **Partial.** Words below confidence 40 are shown to the model as `[unreadable]` with an `OCR_LOW_CONFIDENCE` note; redaction still uses the full line. RapidOCR is now the first engine, with Tesseract as fallback (ADR-0041). On 5 rendered fixtures in a Linux container: RapidOCR 0.981 and Tesseract 5.3.4 1.000 normalised character accuracy. That is synthetic clean text, not held-out real screens. OCR is off by default and no engine is installed on the PC. | `uitext`; `test_uitext.py`, `test_ocr_accuracy_gate.py`. |

## P1 reconciliation

| Audit item | Status | Evidence / gap |
| --- | --- | --- |
| End-to-end cancellation lineage | **Present in code.** | Barge-in cancels the Brain task; that cancellation reaches an in-flight delegation, which cancels its Paperclip issue (tested). The voice turn's trace id now reaches the Brain, the route event, the dispatcher (tool events) and the Paperclip issue, and is the id the latency spans already use for STT and TTS (tested with fakes; not traced on the device). |
| Route observability | **Partial.** | New `BrainRouteSelected` event per routed turn: tier, reason, chain, exclusions, outcome, elapsed ms. Recorded by the flight recorder; no dashboard or export yet. |
| Recovery matrix | **Partial.** | Tested: delegation timeout, cancellation, unreachable Paperclip, missing agent, agent-cancelled issue, empty reply, escalation budget; provider failure after an action started (no replay); completion stall and empty answers spoken; TTS synthesis failure surfaced (`tts_no_audio`). Not yet: injected malformed Step/Qwen responses and rate limits through the new tiers, per-tool timeouts, STT failure recovery on the device. |
| Configuration/onboarding | **Partial.** | Policy is validated config (`BrainRoutePolicyConfig`) written through the normal config path, off by default, escalation explicit and budgeted; commented sample in `jarvis.toml.example`. No in-app UI for it. |
| Cross-platform parity | **Not applicable for this build.** | Single target: Windows 11 on the owner's laptop. A release must name only that combination. |

## Changes made (all pushed to the fork)

| Commit | What |
| --- | --- |
| `5e077c44` fix(voice): never leave a heard turn wedged or mute | Producer teardown deadlock on barge-in with a full sentence queue; completion path always speaks or recovers; TTS failure published as `tts_no_audio`. |
| `a6d5d7eb`, `603d396a` | UI toast for `tts_no_audio`; rebuilt bundle. |
| `bb0f9a19` feat(doctor): tell a silent microphone apart from a quiet one | `classify_mic_level`, doctor microphone check. |
| `7f74cb28` feat(brain): configured fast/deep/escalation routing with Paperclip-only delegation | Routing policy, Paperclip adapter, `BrainRouteSelected`, config models. |
| `fc78f767` fix(brain): never replay a turn after an action tool started | Per-attempt side-effect ledger in `ToolExecutor`; Brain stops fallback and says the action may not have completed. |
| `ac23dfff` feat(screen): keep OCR confidence and mask words it cannot read | OCR confidence policy and accuracy gate. |
| `9c0d255e` fix(telemetry): mask credentials in flight-recorder files and tool errors | `redact_value`; recorder payloads and `ActionExecuted.error` redacted. |
| Freeze pass commit | Missions and their reviewer no longer land on Claude by themselves when the routing policy deny-lists it; release gates, manual steps and evidence updated with the PC findings (microphone root cause, RapidOCR on the PC). |
| `1e56668f` feat(brain): keep images on local models, one trace id per turn, RapidOCR | Images stay on local route targets unless `allow_cloud_vision` (dropped/screen images, tool screenshots, Computer Use); no automatic escalation; recovery lines per language; one trace id per voice turn into the Brain and dispatcher; RapidOCR as the first OCR engine; evidence file and ADR-0039 to ADR-0041. |

## Tests run and results

All runs in a Linux container with the repo's `.venv`; none on the target device.

Final hardening pass (2026-10-03, working tree before commit, local
`jarvis.toml` moved aside where noted):

| Run | Result |
| --- | --- |
| Contract gates: `test_routing.py`, `test_output_filter.py`, `test_hangup_reason_parity.py`, `test_turn_language.py` | 595 passed. |
| New and changed tests: route policy 27, manager routing 6, media/trace 10, tool screenshot 5, Computer Use media privacy 5, OCR (`test_uitext.py` 10 + gate 1), voice trace 3 | All pass. The OCR gate ran for real with RapidOCR: 0.981. |
| `run_tests_parallel.py` over `tests/unit/{brain,cu,harness,screen_context,speech,safety,core,web,telemetry}` (local `jarvis.toml` moved aside) | 8962 passed, 60 failed, 12 skipped; ratchet: 0 new, 60 baselined. |
| Full suite, `run_tests_parallel.py tests --workers 4`, 1298 s | 33545 passed, 284 failed, 247 skipped; ratchet: 159 baselined, 125 not in the baseline across 36 files. Those files are audio devices, Tk/jarvisbar UI, telephony and update-apply tests (container limits, as before). The 6 that differ from a clean `HEAD` worktree all pass once the ignored local `jarvis.toml` is moved aside. |
| Gates | `check_silent_exception_handlers` OK, `check_async_routes` OK, `check_config_switches_wired` OK, `check_public_docs` OK, ruff clean on changed files (manager.py keeps its 31 pre-existing findings, tool_use_loop.py its 6). |

| Run | Result |
| --- | --- |
| New and changed tests (routing 19, manager routing 6, Paperclip 11, no-replay 4, OCR 6, redaction 3, voice 6) | All pass. The no-replay and teardown fixes were shown failing without the change (a second provider ran the action again; teardown hung for 10 s). |
| `tests/unit/brain`, `core`, `safety` via `run_tests_parallel.py` | 5682 passed, 22 failed, 9 skipped. Ratchet: 17 baselined. Of the other 5, three vision tests pass when run alone in both trees (they failed under parallel load), one model-catalog test depends on an ignored local `jarvis.toml` in the working tree (a clean checkout of the base commit fails three tests in that file instead), and the silent-handler gate was fixed before committing. |
| `tests/unit/screen_context` + `tests/unit/safety` | 402 passed. |
| `tests/unit/telemetry` + `safety` + redaction | 140 passed, 1 failed (`test_latency_phase_is_a_string_enum_source_of_truth`, baselined). |
| Full suite, before the P0 commits (`ef3475c8` + work in progress) | 33471 passed, 285 failed, 247 skipped. Ratchet: 159 baselined, 126 not in the baseline across 37 files. |
| Those 37 files on clean checkouts before (`ef3475c8`) and after (`ac23dfff`) | Identical: 68 failed, 270 passed, 61 collection errors on both. Causes are the container (no `tkinter`, no `twilio`, no audio devices), not these changes. |
| Gates | `check_no_new_german` OK, `check_silent_exception_handlers` OK, `check_async_routes` OK, `check_config_switches_wired` OK, ruff clean on new files (manager.py keeps its 31 pre-existing findings). |
| CI | The fork PR does not run `ci.yml`; no CI evidence exists for these commits. |

### Full suite on head

Clean checkout of `ac23dfff`, `run_tests_parallel.py tests --workers 4`, 1282 s:
33512 passed, 282 failed, 251 skipped, no flaky or timed-out files. Ratchet:
164 baselined, 118 not in the baseline. All 118 were already in the 126
non-baselined failures of the run before the P0 commits; none is new, and 8
of the earlier ones now pass. The telemetry redaction commit (`9c0d255e`)
came after this run and was checked with the telemetry and safety suites.

## Release gates (freeze pass, 2026-10-04)

Statuses: PASS, FAIL, ENVIRONMENT-BLOCKED, HARDWARE-BLOCKED, UNVERIFIED. "In
tests" means the evidence is automated tests in the Linux container, not a run
on the PC.

| Gate | Status | Evidence |
| --- | --- | --- |
| Architecture | PASS | `BrainManager` is still the only orchestration boundary. No second voice runtime, router or tool executor was added (ADR-0039). The one new external component is RapidOCR, an optional library behind `uitext` (ADR-0041). |
| Routing | PASS (in tests) | Policy chain is exactly the configured tiers; deny lists apply on every chain; escalation only on a trigger phrase. Missions and their reviewer no longer fall back to Claude when the policy deny-lists it (`_without_automatic_claude`, `_claude_cli_critic_viable`). Routing gate: 363 passed. Not enabled on the PC yet. |
| Privacy | PASS (in tests) | With the policy on, images reach only `local` targets unless `allow_cloud_vision` is set (default false). This covers dropped files, screen context, tool screenshots and every Computer Use engine, including its last resort (ADR-0040). Redaction in the recorder and in pixels. Privacy gate: 63 passed. |
| Tool safety | PASS (in tests) | `ToolExecutor.execute` is the single chokepoint; live voice tools go through it too (`jarvis/realtime/tools.py`). No replay after an action started. Tool-safety gate: 89 passed. |
| Cancellation | PASS (in tests) | Barge-in or hangup cancels the Brain task and an in-flight Paperclip issue; teardown with a full sentence queue does not hang. Cancellation gate: 86 passed. The live Paperclip smoke completed and cleaned up (JAR-16). |
| Failover | PASS (in tests) | One bounded fallback to the other tier; typed recovery lines in the turn's language; no replay of actions. Failover gate: 42 passed. The live free Step route swung from 7 of 11 answered to 24 consecutive 429s, so this path matters. |
| Tracing | PASS (in tests) | One id per voice turn reaches the Brain, the route event, the dispatcher (tool events) and the Paperclip issue. Tracing gate: 44 passed (2 expected failures pre-existing). |
| Regression | PASS | Full suite in the Linux container: 33554 passed, 282 failed, 251 skipped. 164 failures are in the Linux baseline; the other 118 are a subset of the 125 that already failed on `1e56668f` and need Windows, audio devices, Tk or a real git remote. No failure is new to this commit. |
| Voice interruption | HARDWARE-BLOCKED | Root cause found on the PC: Windows audio effects on the Realtek Microphone Array. The raw driver path (WDM-KS) hears the room at -36 dBFS peak, while the shared paths Jarvis uses (WASAPI shared, MME) deliver -130 / -90 dBFS. Privacy permission is Allow, the endpoint is unmuted at 54 %, and the Jarvis log shows -96 dBFS on every heartbeat. The toggle is a protected setting the owner must flip (procedure below). Not application code. |
| Local vision | RESOURCE-BLOCKED (OCR PASS) | Local OCR passes on the PC: RapidOCR 0.981 on the fixtures, about 0.9 s per small image and 3.0 s for a full 1920x1080 screen on the CPU, no downloads, 320 screen-context tests pass. Local visual understanding is blocked: no mmproj on the PC, and about 3.9 GB RAM is free beside the apps while Qwen 35B needs about 21 GB. Cloud vision stays off. |

## Manual steps to close the hardware gates

**Voice (about 2 minutes, at the PC):**
1. Settings > System > Sound > Input: choose "Microphone Array (Realtek(R) Audio)" and set it as the default input (the default is now the silent "Steam Streaming Microphone").
2. Open it > Audio enhancements: Off.
3. "Test your microphone": the bar must move when you speak.
4. Restart Jarvis; the log heartbeat must show `max-rms` above 0, then run the voice checks: say the wake word, ask a long question, interrupt it while it speaks (repeat 10 times), and confirm it stops talking each time and answers the new question.
5. If the bar still does not move: set NahimicService to Manual and start it, or uninstall the Nahimic components (that changes a service, so it is the owner's call).

**Local vision:** needs a vision projector (Qwen3.6 mmproj-F16, 0.84 GB) and a Qwen configuration that runs beside Windows (small context, experts on the CPU, other apps closed), measured before marking the deep tier `local = true`. Until then, image turns with the policy on are answered without a model.

## Remaining blockers

1. Microphone: Windows audio effects silence the shared capture path (see above). Owner action.
2. Step 3.7 Flash free route: the Nous gateway on `127.0.0.1:11436` must be started before `fast` can point at it; its free tier rate-limits heavily at times.
3. Qwen 3.6 35B: the weights are on the PC (checksum verified) but no configuration has run stably. The 65k-context envelope is unverified (the one run returned empty output with 0.08 GB RAM free). The quant is unchanged.
4. Paperclip starts only at its scheduled time after a reboot (decision 5).
5. RapidOCR is validated on the PC in a scratch environment but is not bundled in the installer. Adding it as an extra needs a lockfile refresh, which the container cannot do because the project's custom package index is unreachable from it.
6. Per-tool deadline and in-flight cancellation in the tool executor; tool argument schema validation (upstream gaps, not part of this branch).

## Decisions needed from the owner

1. **Qwen 3.6 35B configuration.** Run it with a small context and experts on the CPU, measured with other apps closed, or keep a free hosted model as `deep` until hardware allows. Paid OpenRouter Qwen is excluded by the free-only rule.
2. **Escalation allowance.** Which phrases trigger Paperclip, the per-session budget (default 5) and the deadline (default 180 s). Nothing escalates on its own.
3. **OCR.** Whether to turn `ocr_enabled` on once RapidOCR is bundled, and the accuracy target for the gate (provisional 0.95).
4. **Cloud vision.** Whether images may go to the hosted fast tier (`allow_cloud_vision = true`). Default and recommendation: off.
5. **Paperclip at startup.** Its scheduled task has only a time trigger; an at-startup trigger would change a Windows task.

## Enabling and rolling back

Enable by adding `[brain.route_policy]` (see the commented sample in
`jarvis.toml.example`) once the `fast` and `deep` endpoints answer. Roll back by
setting `enabled = false` or deleting the block: the Brain then builds chains
exactly as before. Paperclip escalation alone is switched off with
`[brain.route_policy.escalation] enabled = false`.
