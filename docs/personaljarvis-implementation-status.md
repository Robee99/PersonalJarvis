# PersonalJarvis implementation status

Branch `jarvis/local-qwen-build` (fork `Robee99/PersonalJarvis`, draft PR #1).
Last updated 2026-10-03. Evidence for every code reference is in the
[implementation map](personaljarvis-implementation-map.md).

**Verdict: not shippable.** The final hardening pass (2026-10-03) is
recorded in [final-hardening-evidence.md](research/final-hardening-evidence.md)
and ADR-0039 to ADR-0041. The release-gate table below has the current state.
Voice interruption and on-device vision remain blocked by the device (silent
built-in microphone; no stable local deep model). Nothing here claims
measured barge-in reliability or OCR accuracy on real screens.

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
| Final hardening commit (this pass) | Images stay on local route targets unless `allow_cloud_vision` (dropped/screen images, tool screenshots, Computer Use); no automatic escalation; recovery lines per language; one trace id per voice turn into the Brain and dispatcher; RapidOCR as the first OCR engine; evidence file and ADR-0039 to ADR-0041. |

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

## Release gates (final hardening brief, 2026-10-03)

| Gate | Status | Evidence |
| --- | --- | --- |
| Architecture integrity | PASS | `BrainManager` is still the only orchestration boundary; no second voice runtime, router or tool executor was added (ADR-0039). The one new external component is RapidOCR, an optional library behind `uitext` (ADR-0041). |
| Brain routing | PASS (in tests) | Policy chain is exactly the configured tiers; deny lists on every chain; escalation only on a trigger phrase (`on_deep_failure` removed); `test_route_policy*.py` and `tests/unit/brain/test_routing.py` pass. Not enabled on the PC yet. |
| Local-first privacy | PASS (in tests) | With the policy on, images reach only `local` targets unless `allow_cloud_vision` is set. This covers dropped files, screen context, tool screenshots and every Computer Use engine including its last resort (ADR-0040). Tests: `test_route_policy_media_trace.py`, `test_tool_use_loop_image_feedback.py`, `tests/unit/cu/test_brain_call_media_privacy.py`. Without the policy, image routing is unchanged from upstream. |
| Tool safety | PASS (in tests) | `ToolExecutor.execute` is still the single chokepoint; no replay after an action started (`fc78f767`); models never execute OS actions directly (Agent-S-style `exec` rejected). The `tests/unit/safety` suite passes. |
| Voice interruption | BLOCKED | Needs the device; the built-in microphone delivers digital silence. Code paths are unit-tested only. |
| Cancellation | PASS (in tests) | Barge-in cancels the Brain task, which cancels an in-flight Paperclip issue (tested). The live Paperclip smoke completed and cleaned up (JAR-16). No on-device barge test. |
| Vision | BLOCKED | No stable local vision model on the PC (no mmproj; the Qwen 35B run at 65k context returned empty output with 0.08 GB RAM free). OCR: RapidOCR 0.981 on synthetic fixtures in a container, not on the device. NVIDIA NIM rejected. |
| Provider failover | PASS (in tests) | One bounded fallback to the other tier; typed recovery lines in the turn's language. The live Step route went from 7 of 11 answered to 24 consecutive 429s within an hour, so failover matters (evidence §7). Not exercised live through Jarvis. |
| Tracing | PASS (in tests) | One id per voice turn reaches the Brain, the route event, the dispatcher (tool events) and the Paperclip issue (`test_turn_taking.py`, `test_route_policy_media_trace.py`). Not traced on the device. |
| Regression suite | see "Tests run" | Full suite: 33545 passed; every failure is baselined or environmental (local `jarvis.toml`, no audio devices, no Tk, no twilio). Affected suites: 0 new failures. No CI runs on the fork. |
| Live validation | PARTIAL | Step 3.7 Flash (latency, tools, image) and Paperclip delegation passed on the PC; voice and on-device vision are blocked (evidence §10). |

## Remaining blockers

1. Microphone: the built-in Realtek array returns digital silence; suspected Nahimic audio enhancement, the F4 mute key or the BIOS microphone setting. Needs the owner at the PC.
2. Step 3.7 Flash free route: the Nous gateway on `127.0.0.1:11436` must start reliably (its logon task last exited with code 1) before `fast` can point at it. Its free tier rate-limits heavily at times.
3. Qwen 3.6 35B: the weights are now on the PC, but no configuration has run stably yet (see 6).
4. Paperclip starts only at its scheduled time after a reboot (decision 6 below). The live smoke test with the free agent `dan` passed (JAR-16, 46 s).
5. Device voice/barge-in benchmark and latency baseline (gates 4 and 9).
6. Qwen 35B needs a configuration that fits beside Windows and the apps (small context, experts on the CPU), measured with the other apps closed; until then the deep tier has no stable local model.
7. Per-tool deadline and in-flight cancellation in the tool executor; tool argument schema validation.

## Decisions needed from the owner

1. **Qwen 3.6 35B route.** Options: run it locally (the 35B-A3B model at 4-bit is roughly 20 GB, more than the 8 GB GPU plus free RAM on this laptop holds comfortably, so it would be slow); use a smaller local Qwen as the deep tier; or keep a free hosted model (for example Gemini, already configured) as `deep` until hardware allows. Paid OpenRouter Qwen is excluded by the free-only rule.
2. **Escalation allowance.** Which phrases trigger Paperclip, the per-session budget (default 5) and the deadline (default 180 s). A failed deep turn no longer escalates on its own.
3. **OCR.** Whether to install RapidOCR on the PC (`pip install rapidocr` in the Jarvis environment) and turn `ocr_enabled` on, and the accuracy target for the gate (provisional 0.95).
5. **Cloud vision.** Whether images may go to the hosted fast tier (`allow_cloud_vision = true`). Until a local deep model with an mmproj runs stably, the alternative is that image turns are answered without a model.
6. **Paperclip at startup.** Its scheduled task has only a time trigger, so after a reboot Paperclip is down until that time (escalation then returns a typed "unavailable" in about 2 s). An at-startup trigger would change a Windows task.
4. **Mission workers.** Whether missions should also be barred from direct Claude workers.

## Enabling and rolling back

Enable by adding `[brain.route_policy]` (see the commented sample in
`jarvis.toml.example`) once the `fast` and `deep` endpoints answer. Roll back by
setting `enabled = false` or deleting the block: the Brain then builds chains
exactly as before. Paperclip escalation alone is switched off with
`[brain.route_policy.escalation] enabled = false`.
