# PersonalJarvis implementation status

Branch `jarvis/local-qwen-build` (fork `Robee99/PersonalJarvis`, draft PR #1).
Last updated 2026-10-03. Evidence for every code reference is in the
[implementation map](personaljarvis-implementation-map.md).

**Verdict: not shippable.** The P0 code items that can be built and tested
without the target device are in place and tested. Release gates 1 (partly),
3 and 7 (partly) have evidence; gates 2, 4, 5, 6, 8 and 9 do not. Nothing
below claims measured latency, barge-in reliability, OCR accuracy on real
screens, or live model behaviour.

## Preserved architecture decisions

- The JARVIS Brain (`BrainManager`) stays the single orchestration boundary.
  Routing is a pure policy function (`jarvis/brain/route_policy.py`) that the
  Brain calls while building each turn's chain; no second router or service.
- The voice stack is unchanged apart from the lifecycle fixes listed below; no
  new voice framework.
- Model choice is configuration- and capability-driven. Provider and model
  names appear only in `[brain.route_policy]`; the policy never compares
  against a provider or model id (AP-21).
- Claude is reached only through Paperclip when the policy is on: deny lists
  remove direct Claude providers from every chain, and escalation creates a
  Paperclip issue instead of opening a model client.

## P0 reconciliation

| Audit item | Before this work | Now | Evidence |
| --- | --- | --- | --- |
| Three-stage routing (Step -> Qwen -> Paperclip Claude) | **Missing.** Generic primary + fixed `cross_order` fallback; `claude-api` first in the built-in order. | **Present in code, not live.** Opt-in `[brain.route_policy]` with `fast`, `deep`, `escalation` tiers, deny lists, capability/availability exclusion with reasons, one bounded fallback, never repeats a target, deterministic test override only under `JARVIS_ROUTE_POLICY_TEST_MODE=1`. Not enabled on the PC because the Step and Qwen endpoints are not reachable yet. | `route_policy.decide_route`; `BrainManager._policy_chain`, `_apply_route_deny`; tests `test_route_policy.py` (19), `test_route_policy_manager.py` (6). |
| Paperclip contract | **Missing.** MCP connector only. | **Present in code; live smoke test not run.** `PaperclipDelegate`: resolves company and agent, creates one issue with redacted bounded context, the turn id and an `idempotencyKey`, polls status, reads the agent's last comment, cancels the issue on deadline, cancel token or task cancellation (barge-in), maps every outcome to a typed failure and a user-safe message, per-session escalation budget. Routes and fields checked against Paperclip's own validators and the PC's running instance. | `paperclip_delegation.py`; `BrainManager._escalate_to_delegate`; `test_paperclip_delegation.py` (11), `test_route_policy_manager.py` escalation cases. |
| Qwen/Step endpoint compatibility | **Unknown.** | **Recorded; blocked.** Step 3.7 Flash free exists only through Nous via the owner's loopback gateway (`127.0.0.1:11436`, OpenAI chat-completions, SSE, `max_tokens` <= 4096, 429 fair-share limits seen); the gateway is not running (its logon task last exited with code 1). OpenRouter lists Step 3.7 Flash and Qwen 3.6 35B-A3B as paid only, which the free-only rule excludes. No Qwen 3.6 35B weights on the PC. | Map: "Models". |
| Target-device release evidence | **Missing.** | **Missing.** The built-in microphone still delivers digital silence on the PC, so no voice run is possible yet. | Map: "Voice", mic row. |
| OCR/vision accuracy gate | **Missing.** OCR passed every word as fact; one `OCR_UNAVAILABLE` code; no fixtures. | **Partial.** Words below confidence 40 are shown to the model as `[unreadable]` with an `OCR_LOW_CONFIDENCE` note telling it not to guess; the prompt says OCR text can misread; redaction still uses the full line. Labelled-fixture gate added (skips without an engine). Measured 1.000 normalised character accuracy on 5 rendered fixtures with Tesseract 5.3.4 in a Linux container: synthetic clean text, not held-out real screens. OCR is off by default and no engine is installed on the PC. Vision capability is still self-declared by providers. | `uitext.ocr_supplement_with_regions`; `test_uitext.py` (+5), `test_ocr_accuracy_gate.py`. |

## P1 reconciliation

| Audit item | Status | Evidence / gap |
| --- | --- | --- |
| End-to-end cancellation lineage | **Partial.** | Barge-in cancels the Brain task; that cancellation reaches an in-flight delegation, which cancels its Paperclip issue (tested). There is still no single `turn_id` from STT through Brain, tools, Paperclip and TTS: the pipeline passes no trace id into the Brain and the dispatcher mints its own. |
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

## Tests run and results

All runs in a Linux container with the repo's `.venv`; none on the target device.

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

## Release gates (spec and audit)

| Gate | Status |
| --- | --- |
| 1 Baseline on the exact commit, pre-existing failures separated | Partial: done locally (above); not in CI. |
| 2 Model contracts + one live smoke per provider | Missing: Paperclip contract tests only; no Step or Qwen endpoint to test; no live smoke run. |
| 3 Routing: curated cases 100%, direct Claude impossible in test | Met for the policy fixtures and deny-list tests. Mission workers can still use `ClaudeDirectWorker` (outside the deny list). |
| 4 Voice: 30+ barge trials, zero stale audio | Missing: needs the device; microphone currently silent. |
| 5 Recovery matrix | Partial (see P1). |
| 6 OCR/vision on held-out labelled fixtures | Partial: synthetic fixtures only; no agreed target; no engine on the PC. |
| 7 Safety/privacy | Partial: approvals unchanged; delegation context redacted and capped; recorder files and tool errors redacted. `ActionProposed.args` is still raw on the live bus (the approval card needs it). |
| 8 Operational: route export, flags to disable Paperclip, vision, providers | Partial: `escalation.enabled`, `deny_providers`, `[screen_context].enabled` and `ocr_enabled` exist; route events are recorded but there is no dashboard or export, and dropped images still reach vision models without a separate switch. |
| 9 Platform statement | Not written yet; only Windows 11 on the owner's laptop is in scope. |

## Remaining blockers

1. Microphone: the built-in Realtek array returns digital silence; suspected Nahimic audio enhancement, the F4 mute key or the BIOS microphone setting. Needs the owner at the PC.
2. Step 3.7 Flash free route: the Nous gateway on `127.0.0.1:11436` must start reliably (its logon task last exited with code 1) before `fast` can point at it.
3. Qwen 3.6 35B: no free hosted route and no local weights; see product decisions.
4. Live Paperclip smoke test: run one escalation against a free Paperclip agent first (for example `dan`), never the Claude agent, so no Claude usage is spent while testing.
5. Device voice/barge-in benchmark and latency baseline (gates 4 and 9).
6. A shared `turn_id` across STT, Brain, tools, Paperclip and TTS (P1 lineage).
7. Per-tool deadline and in-flight cancellation in the tool executor; tool argument schema validation.

## Decisions needed from the owner

1. **Qwen 3.6 35B route.** Options: run it locally (the 35B-A3B model at 4-bit is roughly 20 GB, more than the 8 GB GPU plus free RAM on this laptop holds comfortably, so it would be slow); use a smaller local Qwen as the deep tier; or keep a free hosted model (for example Gemini, already configured) as `deep` until hardware allows. Paid OpenRouter Qwen is excluded by the free-only rule.
2. **Escalation allowance.** Which phrases trigger Paperclip, whether a failed deep turn may escalate (`on_deep_failure`), the per-session budget (default 5) and the deadline (default 180 s).
3. **OCR.** Whether to install an OCR engine on the PC and turn `ocr_enabled` on, and the accuracy target for the gate (provisional 0.95).
4. **Mission workers.** Whether missions should also be barred from direct Claude workers.

## Enabling and rolling back

Enable by adding `[brain.route_policy]` (see the commented sample in
`jarvis.toml.example`) once the `fast` and `deep` endpoints answer. Roll back by
setting `enabled = false` or deleting the block: the Brain then builds chains
exactly as before. Paperclip escalation alone is switched off with
`[brain.route_policy.escalation] enabled = false`.
