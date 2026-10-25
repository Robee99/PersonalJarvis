# Eyes and Screen Watch Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans in this session. Steps use checkbox syntax for tracking.

**Goal:** Add the PDF's independent webcam eyes and desktop screen-watch organs to existing Jarvis.
**Architecture:** Browser-held independent video streams, transient labelled image inputs on existing agent-chat turns, and local thumbnail comparison for watch timers. Reuse BrainManager's image injection and selected-provider override; no standalone runtime.
**Tech Stack:** Existing React/TypeScript, browser media APIs, FastAPI/Pydantic and Jarvis BrainManager.
**Spec:** User-provided Build-Your-Own-Jarvis-GPT-6-Astra-Prompt-Pack.pdf, pages 10, 18 and 19; limited by the user's vision-only scope and no installs, paid calls, user-data changes, merge or deployment.

## Global Constraints

- Camera and display sharing are independent; neither opens the microphone.
- Screen Watch compares tiny local thumbnails every five seconds, resets on change, asks for one useful nudge after 60 seconds unchanged and cools down for three minutes.
- A live share supplies fresh question-time JPEG frames. Camera questions use webcam; screen questions use display. Both can be requested together.
- Webcam pose signals remain local. Required face/pose assets must already exist; otherwise expose unavailable tracking without inventing signals or downloading assets.
- A sustained phone/slouch posture uses 700 ms and a 30-second cooldown; absence uses 12 seconds. Relief phrases silence nudges for three minutes.
- A tab/window selection is labelled honestly; entire-screen watch requires a monitor selection.
- Background observation must use a keyless local provider with no paid fallback, cannot run tools, and must not store raw frames in chat events or files.
- Existing permission, screen privacy, cancellation and session boundaries remain binding. Frame capture failures must be reported, never answered from old frames.
- Nemotron's 128K allocation is experimental; previous synthetic image checks do not prove long-input reliability. Do not change installed model settings.

## Review Focus

1. A permission picker resolving after Stop must have every returned track stopped.
2. Navigation, source-ended events and session changes must invalidate pending frames/nudges.
3. Repeated quiet ticks must not overlap inference or issue repeated nudges during cooldown or relief.
4. Malformed, oversized, stale, duplicate-source or wrong-session frames must be refused before the turn starts.
5. Background visual work must refuse a paid/nonlocal provider and must have no action tools or raw-image persistence.

### Task 1: Transient image contract and existing brain integration

Files: new `jarvis/vision/live_inputs.py`; existing agent-chat routes, service and runner; `tests/unit/vision/test_live_inputs.py` and runner tests.
Interface: `LiveVisionFrame` validates `source`, `mime`, `data_b64`, `captured_at_ms`; `decode_frames()` produces bounded `ImageBlock`s. `vision_frames` and `vision_nudge` are transient send arguments, passed only to `run_brain_turn`.
- [ ] Write and run failing validation and image-injection tests.
- [ ] Implement bounded validation, local-provider background guard and trace-scoped injection/cleanup using the existing manager.
- [ ] Run new and adjacent offline suites; commit explicit paths.

### Task 2: Independent media and watch state

Files: new frontend `lib/liveVision.ts`, `lib/liveVision.test.ts`, and `components/agentchat/VisionControls.tsx`.
Interface: `LiveVisionController` owns camera/display streams, fresh captures, watch state and Stop. A React hook/component owns lifecycle and displays independent states. Local tracking is a separate capability, never a model-generated posture guess.
- [ ] Write failing tests for independent sources, fresh frames, late picker resolution, stop, source end, screen-change timing, cooldown and relief.
- [ ] Implement native media bindings and local watch comparisons. Report unavailable landmarks honestly if no pre-existing face/pose model is present.
- [ ] Run focused frontend tests and commit explicit paths.

### Task 3: Wire the real composer, verify and review

Files: existing composer, store and API; integration tests; diagnostics/report.
- [ ] Write a failing API/runner regression proving actual image bytes reach the selected turn and are absent from persisted events.
- [ ] Wire fresh source selection to question submission, watch nudges to the same chat, microphone-off feedback and mute-aware existing speech delivery.
- [ ] Run scoped backend/frontend regressions, required contract guards and the production frontend build. Compare any failure against starting commit 863b231926804aff96509e5d63b1d72fcef700d3.
- [ ] Independently review the full diff, address important findings with red/green regressions, commit and export evidence. Do not merge or deploy.

## Progress

Starting branch: integration/jarvis-suite-repair-20261024, clean at 863b231926804aff96509e5d63b1d72fcef700d3. Existing vision baseline: 345 pass, two skips, two independently reproduced starting-commit failures in test_manager_screen_context.py. Both local models passed four synthetic-image cases each, using installed projectors and a temporary catalog read from actual server metadata.

Ruling: the user's instruction to refer to the PDF replaces the earlier snapshot-only proposal. Implement the referenced vision requirements directly rather than request another design approval.
