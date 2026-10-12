# Final hardening evidence

Branch `jarvis/local-qwen-build` of `Robee99/PersonalJarvis`. Research,
decisions and measurements for the final hardening pass, all checked on
2026-10-03. Companion documents: the
[implementation status](../personaljarvis-implementation-status.md) (release
gates) and the [implementation map](../personaljarvis-implementation-map.md).

**Evidence types.** **Direct** means read in source code, a licence file, a
package index, official documentation, or measured. **Inferred** means
derived from direct facts. **Unverified** means it rests on a third-party
claim or was not checked. Line counts are `wc -l` over `*.py` at commit
`5d4403b1` unless stated otherwise.

**Where the evidence came from.**
- Repositories were read from shallow clones made on 2026-10-03.
- Facts about the owner's PC come from a session running on that PC (Windows
  11, Ryzen 7 7840HS, RTX 4060 8 GB, 16 GB RAM).
- Container measurements come from a 4-vCPU Linux container and say so.
- Model pages were read through a web fetcher. huggingface.co, nvidia.com and
  openrouter.ai are blocked for direct HTTP from the container, so their exact
  numbers are marked as fetched rather than measured.

## 1. Answers first

| Question | Answer | Evidence |
| --- | --- | --- |
| Is Hermes Voice Mode alone sufficient for the voice layer? | **No.** It is a push-to-talk CLI feature that feeds its transcript into Hermes's own agent loop. It has no library interface for an external brain, its turn detection is an RMS threshold with a fixed 3.0 s auto-stop, and its default TTS (`edge`) is a Microsoft online service. | §3 |
| Could Hermes, behind the PJ Brain and tool layer, eliminate a significant amount of custom voice code? | **No.** It would replace none of PJ's wake, VAD, endpointing, barge-in, echo, device recovery or STT/TTS fallback code. To use it at all, Hermes's agent loop would have to be cut out, and PJ's Brain/tool boundary would have to be re-hosted inside it. | §3, §4 |
| Is there a single repository, or a small set, that removes a substantial part of the remaining implementation? | **No for voice, agent runtime and safety. Yes for one narrow piece: OCR.** RapidOCR replaces the need for a native Tesseract install and gives per-word boxes and scores, which the redaction path needs. pipecat and livekit-agents would replace the pipeline only by also replacing the Brain/tool boundary. | §5, §6 |
| Smallest reliable combination | The existing PJ voice pipeline and Brain, the route policy (Step 3.7 Flash fast tier through the owner's loopback gateway, a local deep tier when one exists, Claude only through Paperclip on explicit request), RapidOCR for local OCR, and the Smart Turn v3.2 model already vendored in the voice engine. | §6, §7 |
| Was anything built that the ecosystem already solves? | No. The code changes in this pass are policy and wiring inside PJ: a media privacy gate, one trace id per turn, localized recovery lines, and an OCR adapter around RapidOCR. | §9 |

## 2. Findings register

| # | Source and URL | Observed behavior | Evidence | Confidence | PJ relevance | Decision | Affected |
| --- | --- | --- | --- | --- | --- | --- | --- |
| F1 | Hermes Agent v0.21.3, installed at `%LOCALAPPDATA%\hermes\hermes-agent`, HEAD `274bc7b8f6` (2026-09-21), MIT; https://github.com/NousResearch/hermes-agent | Voice mode is push-to-talk ("push-to-talk recording and playback for the CLI", `tools/voice_mode.py:1`). Turn end is RMS < 200 for 3.0 s (`voice_mode.py:34-35, 606-682`). Barge-in is an RMS `_BargeDetector` (`voice_mode.py:1108-1402`) calling `agent.interrupt()`. The transcript goes to Hermes `chat()`/`process_loop` (`cli_voice_mixin.py:207-296`). Default TTS is `edge` (cloud); local options are neutts, piper and kittentts. STT defaults to local; cloud options are groq, openai, mistral, xai and elevenlabs. | Direct (source on the PC) | High | Candidate voice layer | **REJECT** as runtime | ADR-0039 |
| F2 | PJ voice stack at `5d4403b1` | `jarvis/speech` 39 files, 28,278 lines (`pipeline.py` 18,632); `jarvis/audio` 7,671; `jarvis/voice_engine` 4,442; `jarvis/realtime` 26,140. Has a 7-state `TurnTakingState`, Silero, then WebRTC, then RMS VAD; echo guard; wake word; STT/TTS fallback; device recovery. | Direct (measured) | High | Baseline | Keep | — |
| F3 | pipecat, https://github.com/pipecat-ai/pipecat, BSD-2-Clause | A full frame-pipeline framework (about 193k lines). It would bring a second event model, and PJ's barge, echo, wake and device logic would have to be re-hosted as processors. | Direct (clone) | High | Voice runtime candidate | **REJECT** as runtime | ADR-0039 |
| F4 | livekit/agents, https://github.com/livekit/agents, Apache-2.0 code, LiveKit Model License on turn-detector weights | Agent framework with its own tool executor and session model, built around WebRTC rooms. | Direct (clone, licence files) | High | Voice runtime candidate | **REJECT** (own tool executor; weight licence is not MIT/BSD/Apache/CC0) | ADR-0039 |
| F5 | RealtimeSTT / RealtimeTTS (KoljaB), MIT | Wrappers over faster-whisper/Silero and a set of TTS engines. PJ already owns the same layers with fallback. | Direct (clone) | Medium | Overlap | **REJECT** (RealtimeTTS engines **DEFER**) | — |
| F6 | TEN framework, https://github.com/TEN-framework/ten-framework | Its licence restricts hosting on end-user devices. | Direct (licence) | High | Voice runtime candidate | **REJECT** | — |
| F7 | Smart Turn v3.2 (pipecat-ai/smart-turn), BSD-2-Clause | Already vendored: `jarvis/voice_engine/turn.py` (106 lines). The model is pinned at `voice_engine/models.py:68-76` with the same sha256 as pipecat's `smart-turn-v3.2-cpu.onnx` (8.68 MB). Only the voice engine uses it (`runtime.py:93`). The classic pipeline's `SileroEndpointer` waits a fixed 1.5 s (`pipeline.py`, `SileroEndpointer`). Measured 74 ms median / 134 ms p90 per inference on a container CPU. | Direct (source, measured) | Medium | Up to about 1 s less end-of-turn wait (inferred, not measured) | **DEFER** wiring into the classic pipeline until there are on-device recordings to tune it; German accuracy is unverified | §8 |
| F8 | WebRTC APM acoustic echo cancellation | PJ has no AEC, only a text-similarity echo guard. | Direct (source) | Medium | Barge-in quality | **DEFER** (needs the device and a working mic) | — |
| F9 | RapidOCR 3.9.2, https://github.com/RapidAI/RapidOCR, Apache-2.0 code; PP-OCRv6 weights Apache-2.0 (`python/MODEL_LICENSES.md`) | The wheel bundles det/cls/rec ONNX models, so the default use downloads nothing. It runs on onnxruntime, which is already a base PJ dependency. `return_word_box=True` gives (word, score, quad). Its default `text_score` 0.5 drops low-score lines. | Direct (source, wheel, run) | High | Local OCR engine | **ADOPT** as the first engine (optional install) | ADR-0041, `uitext.py` |
| F10 | RapidOCR measured in the container (4 vCPU) | 0.981 normalised character accuracy on the 5 rendered UI fixtures of `test_ocr_accuracy_gate.py` (target 0.95). About 2.1 s median per small image on that CPU. | Direct (measured) | Medium (synthetic text, container CPU) | Accuracy gate | Recorded | §10 |
| F11 | Tesseract + pytesseract, Apache-2.0 | Per-word boxes with 0-100 confidence. Needs a native binary that is not on PyPI. pytesseract's last commit was 2025-02-17. | Direct | High | Existing engine | Keep as fallback | `uitext.py` |
| F12 | Windows.Media.Ocr via PyWinRT (MIT), `winrt-Windows.Media.Ocr` 3.2.1 | `OcrWord` has only `bounding_rect` and `text`, with no confidence at any level (`_winrt_windows_media_ocr.pyi:43-70`). PJ's masking would silently pass every word. | Direct | High | Zero-install fallback | **DEFER** (no confidence means no masking) | — |
| F13 | PaddleOCR, Apache-2.0 | Needs the paddlepaddle framework; the models download from HF/Baidu. RapidOCR packages the same models without the framework. | Direct | High | OCR | **REJECT** (weight) | — |
| F14 | microsoft/OmniParser | Repo licence CC-BY-4.0 while the badge says MIT; earlier detector weights are AGPL. Imports EasyOCR and PaddleOCR at import time; pulls torch, ultralytics, openai and anthropic. | Direct | High | UI element detection | **REJECT** now; the icon-detector idea is **DEFER** | — |
| F15 | bytedance/UI-TARS-desktop, Apache-2.0 | TypeScript/Electron app driven by UI-TARS or Doubao VLMs; a 7B VLM does not fit next to STT/TTS in 8 GB (inferred). | Direct / inferred | Medium | Computer use | **REJECT** | — |
| F16 | simular-ai/Agent-S, Apache-2.0 | Has its own brain (`gpt-5-2025-08-07` default, `gui_agents/s3/cli_app.py:233,239`) and runs model code with `exec(code[0])` (`cli_app.py:215`). | Direct | High | Computer use | **REJECT** (models must never execute OS actions directly) | — |
| F17 | OpenInterpreter/open-interpreter | Main branch is now a Rust Codex fork; the Python `computer` API is gone. | Direct | High | Computer use | **REJECT** | — |
| F18 | mediar-ai/screenpipe | The "Screenpipe Commercial License" forbids embedding in a product. | Direct | High | Screen capture | **REJECT** | — |
| F19 | openai/codex `execpolicy` (Apache-2.0, Rust) | Prefix rules with allow/prompt/forbidden decisions, plus example commands validated at load time. | Direct | Medium | Shell risk tiers | **ADAPT** the idea later (no Python binding); no code now | — |
| F20 | anthropic-experimental/sandbox-runtime 0.0.78, Apache-2.0 | On Windows it runs commands as a dedicated user with an egress filter. Needs a one-time elevated install. | Direct (README) | Medium | Shell sandbox | **DEFER** (would change Windows settings; needs an owner decision) | — |
| F21 | Step 3.7 Flash model card, https://huggingface.co/stepfun-ai/Step-3.7-Flash | About 198B total parameters, 11B active, Apache-2.0, 256K context, text and image in, tool calling. Smallest GGUF is about 102 GB; StepFun asks for at least 120 GB. | Direct (fetched) | High | Fast tier | Hosted only | §7 |
| F22 | Step 3.7 Flash live on the PC through the owner's Nous gateway (`127.0.0.1:11436`, free) | See §7: first token 1.9–3.0 s, tool call valid, image read correctly, 4 of 11 requests rate-limited. | Direct (measured) | Medium (11 requests) | Fast tier | **ADOPT** as the fast tier (cloud, not local) | §7 |
| F23 | Qwen3.6-35B-A3B, https://huggingface.co/Qwen/Qwen3.6-35B-A3B, Apache-2.0; PC file check | 35B total, 3B active, 30 of 40 layers linear attention, native vision through an mmproj (0.84 GB F16). UD-Q4_K_XL (20.82 GiB, SHA-256 verified) is on the PC; a 65k-context run there returned empty output with 0.08 GB RAM free. | Direct (fetched, PC) | High | Deep tier | Quant change **DEFER** | §8 |
| F24 | NVIDIA API catalog, https://build.nvidia.com and the API Trial Terms of Service | Free endpoints exist for some VLMs (e.g. llama-3.2-90b-vision-instruct, paligemma). The terms say "internal testing and evaluation purposes, not in production" and forbid uploading personal information. Rate limits are unpublished and revocable. | Direct (fetched) | Medium | Cloud vision fallback | **REJECT** | ADR-0040 |
| F25 | Qwen3-VL-2B/4B, Apache-2.0, official GGUF | OCRBench 858 / 881 (tech report). Q4_K_M 1.1 / 2.5 GB plus mmproj. | Direct (fetched) | Medium | Separate local VLM | **DEFER** (no on-device measurement; Qwen3.6 already has vision) | §8 |
| F26 | Codacus `thecodacus/dexter` (closest match for "CodeAcu"), Apache-2.0, Rust | macOS-only push-to-talk assistant (whisper-rs, Ollama, Chatterbox); sentence-chunked TTS; vision only as an on-demand tool. Interruption is not documented. | Direct (repo) / inferred identity | Low | Patterns | **REJECT** as code; PJ already streams per sentence and treats vision as on demand | — |
| F27 | Zubair Trabzada, "I Built a Real Life JARVIS with Claude Fable 5.1" (2026-09-02) | Claude-brained demo with SaaS connectors and Retell telephony. The code is paywalled; there is no public repo. No barge-in or recovery evidence. | Direct (transcript summary) | Low | Patterns | **REJECT** (nothing reusable, and Claude-first conflicts with the routing rules) | — |
| F28 | Paperclip live smoke on the PC (free agent `dan`) | Fork at `5d4403b1` with `PaperclipDelegate`: completed, issue JAR-16, 46.1 s, reply "PONG", issue done. With Paperclip down after a reboot, the delegate returned typed UNAVAILABLE in 2.3 s. | Direct (measured) | High | Escalation path | Keep explicit-only | §10 |
| F29 | PJ code: images and the route policy (`BrainManager._generate`, `_lead_vision_chain`, `_hoist_tool_model`, `ToolUseLoop` image feedback, `ComputerUsePlannerSelector`, `iter_last_resort_vision`) | With the policy on, images could still reach a hosted model by four paths. (1) A screenshot or dropped image led to any vision-capable provider, since `_lead_vision_chain` and `_hoist_tool_model` pulled targets from outside the tiers. (2) A screenshot taken by a tool mid-turn was fed back to whichever model ran the turn. (3) Computer Use sent each step's screenshot to the `fast` chain, which is the hosted Step tier. (4) Its last resort tried every registered vision provider. The fast tier is a hosted model behind a loopback URL. | Direct (source) | High | Silent cloud transmission | **ADOPT** a fix | §9.1 |
| F30 | PJ code: voice turn id | The pipeline's per-turn `LatencyTracker.trace_id` was not passed to the Brain; the dispatcher received the caller's `trace_id` (often None) rather than the turn's. | Direct (source) | High | Per-turn trace | **ADOPT** a fix | §9.2 |
| F31 | PJ code: `on_deep_failure` | A failed deep turn could escalate to Paperclip (Claude) without the user asking. | Direct (source) | High | Claude explicit-only | **ADOPT** removal | §9.4 |
| F32 | PJ code: recovery lines | `RECOVERY_MESSAGES` were English only; voice turns can be German or Spanish. | Direct (source) | High | Recovery speech | **ADOPT** localisation | §9.4 |
| F33 | Duplicate tool-call suppression | `seen_call_ids` already drops repeated call ids. Repeating the same signature is legitimate in computer use (scroll, Tab), and ask-tier tools need approval per call. | Direct (source) | High | Idempotency | **REJECT** signature dedupe | — |
| F34 | Rate-limit cooldown | `_rate_tracker.mark_rate_limited` uses a fixed 30 s cooldown; Nous returns `retry_after` of 3–33 s. | Direct | Medium | Failover | **DEFER** (small gain; the next tier answers meanwhile) | — |
| F35 | `jarvis/harness/screenshot_only_loop_june13.py` (2,916) and `_stable.py` (4,597) beside `screenshot_only_loop.py` (5,386) | They look like duplicates but are not dead: the engine switch and its tests import them (`tests/unit/harness/test_cu_engine_switch.py`, `scripts/cu_test_rig.py`). | Direct | High | Duplicate code | **DEFER** consolidation (product engine switch) | — |
| F36 | PJ code: mission workers and critic (`jarvis/missions/init.py` `_worker_factory`, `_cross_family_last_resort_worker`; `jarvis/missions/critic/runner.py`), freeze pass 2026-10-04 | The default, fallback and last-resort mission worker is the `claude` CLI (or the Anthropic key) whenever it is reachable, and the critic prefers the Claude CLI. That holds even with the route policy deny-listing Claude, so a mission could spend Claude usage without being asked. | Direct (source) | High | Claude explicit-only | **ADOPT** a guard: with Claude deny-listed, a Claude worker or critic is swapped for another reachable family, or the mission fails with a reason | `tests/missions/test_worker_claude_reserved.py` |
| F37 | PC microphone, freeze pass 2026-10-04 (read-only checks) | The same Realtek Microphone Array hears the room through the raw driver path (WDM-KS, -36.5 dBFS peak) but is digital silence through WASAPI shared (-130 dBFS) and MME (-90 dBFS), the paths Jarvis uses. Privacy consent is Allow; the endpoint is unmuted at 54 %; audio enhancements are on with three effects in the chain while NahimicService is disabled; the Windows default input is the silent Steam Streaming Microphone. The Jarvis log shows -96 dBFS on every heartbeat. | Direct (measured) | High | Voice gate | **HARDWARE-BLOCKED** (protected Windows setting); WDM-KS capture stays excluded (PortAudio's blocking API crashes on it, `capture.py` `_HOSTAPI_BLOCKLIST`) | status doc, manual steps |

## 3. Hermes Voice Mode

### Decision matrix

| Capability | PersonalJarvis today | Hermes Voice today | Evidence | Can reuse directly? | Integration risk | Decision |
| --- | --- | --- | --- | --- | --- | --- |
| STT | Local Whisper path plus cloud options, provider fallback (`jarvis/speech`) | Batch STT after push-to-talk; default `local`, cloud options groq/openai/mistral/xai/elevenlabs | Direct | No: batch only, no streaming partials | Medium | REJECT |
| TTS | Sentence-streamed synthesis with a fallback chain; `tts_no_audio` surfaced | Default `edge` (Microsoft online TTS, cloud); local neutts/piper/kittentts; `tts_streaming.py` 329 lines | Direct | No: the default is cloud, and PJ already has local engines | Medium | REJECT |
| Barge-in | VAD barge detectors, output cancel, Brain task cancel, interrupt-intent filter | RMS `_BargeDetector` calling `agent.interrupt()` | Direct | No: interrupts Hermes's agent, not an external brain | High | REJECT |
| Cancellation | Brain task cancel propagates into tools and a Paperclip delegation (cancels the issue) | `agent/interrupt_control.py` (290 lines) for Hermes's own agent; no turn-id contract | Direct | No | High | REJECT |
| Turn detection | Silero, then WebRTC, then RMS VAD; Silero endpointer; Smart Turn v3.2 in the voice engine | RMS < 200 for 3.0 s | Direct | No: weaker | Low | REJECT |
| Voice state | `TurnTakingState`, 7 states, published to the UI | No explicit state machine | Direct | No | — | REJECT |
| Model routing | Route policy (fast/deep/escalation), deny lists, capability gates | Hermes's own provider config | Direct | No: would bypass the Brain | High | REJECT |
| Tool execution | `ToolExecutor.execute`: risk tiers, approval, voice confirm, no replay | Hermes's own tool loop | Direct | No: would bypass the local safety boundary | High | REJECT |
| Vision | Screen context with accessibility text, OCR, redaction; media privacy gate | Not part of voice mode | Direct | — | — | REJECT |
| Recovery | Stall and ceiling guards, spoken provider-down and recovery lines, typed delegate failures | Not documented beyond retries in the agent | Inferred | No | — | REJECT |

### Hermes questions A–H

- **A. What Hermes already solves.** Push-to-talk recording, an RMS barge-in detector, local wake word (openWakeWord, sherpa, Porcupine), several TTS and STT backends, and a text-similarity echo filter (threshold 0.6).
- **B. What PersonalJarvis already solves better.** Streaming turn-taking with a state machine, layered VAD, endpointing, device recovery, sentence-streamed TTS with fallback, cancellation that reaches tools and Paperclip, and the safety boundary for tools.
- **C. What Hermes would duplicate.** STT/TTS backend selection, wake word, barge-in and echo handling: every voice layer PJ already has.
- **D. What Hermes could replace.** Nothing without loss. The only reusable pieces are ideas, for example its echo text filter, which PJ's `echo_guard.py` (189 lines) already covers.
- **E. What Hermes cannot provide.** A library interface for an external brain, a turn-id contract, streaming STT, a voice state machine, a local-only default TTS, or PJ's tool approval flow.
- **F. Is integration cleaner than the current implementation?** No. It would need an adapter that cuts out Hermes's `chat()`/`process_loop` and re-routes the transcript to PJ's Brain, and that adapter would have to track Hermes releases.
- **G. Does Hermes introduce architectural coupling?** Yes: to Hermes's CLI process model, its config and its agent loop.
- **H. Does it preserve the Brain abstraction, local tool boundary, local-first privacy, Step/Qwen routing and explicit Claude boundary?** Not as shipped. Its default TTS is cloud, and its agent would own tools and routing.

## 4. Options A–E (factual comparison)

| | A: Hermes Voice + existing PJ | B: one external runtime (pipecat or livekit-agents) | C: Hermes + one specialised repo | D: Hermes + a small set | E: existing PJ (chosen) |
| --- | --- | --- | --- | --- | --- |
| Code eliminated | None measurable; PJ's voice layers stay because Hermes lacks them (§3) | Up to the pipeline orchestration in `pipeline.py` (18,632 lines), but barge, echo, wake and device logic (about 11k lines in `jarvis/speech` + `jarvis/audio`) must be re-hosted as processors (inferred) | As A | As A | None |
| Code added | An adapter around Hermes's CLI voice loop plus a brain bridge | Processors for the Brain, tools, wake, barge and echo; a second event model | A plus that repo's adapter | A plus several adapters | This pass: about 300 lines of production code and 400 of tests |
| Integration complexity | High: cut out Hermes's agent | Very high: re-host the Brain/tool boundary | High | Highest | Low |
| Privacy | Default TTS is cloud | Neutral; livekit assumes rooms | As A | As A | Local-first; media gate added |
| Security | Hermes tool loop beside PJ's | Second tool executor (livekit) | As A | As A | One chokepoint (`ToolExecutor`) |
| Performance | RMS 3.0 s auto-stop is slower than PJ's endpointing | Comparable; unmeasured | As A | As A | Known; Smart Turn could cut up to about 1 s (inferred) |
| Maintenance | Track Hermes releases | Track a large framework | Two upstreams | Several | PJ only |
| Licensing | MIT | BSD-2 (pipecat); LiveKit Model License on the turn-detector weights | Depends | Depends | — |
| Evidence quality | Direct (source on the PC) | Direct (clones) | Direct | Direct | Direct |
| Migration risk | High | Very high | High | Very high | None |

**Selected: E**, plus RapidOCR for OCR and the already vendored Smart Turn
model. No option removes more code than it adds without giving up the Brain
abstraction or the tool boundary.

## 5. Repository shortlist

| Repository | Licence | Last update | Language | Relevant capability | Working evidence | Dependencies / hardware | Cloud / privacy | Security model | Integration surface | Reuse estimate | Maintenance risk | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| NousResearch/hermes-agent | MIT | 2026-09-21 (installed HEAD) | Python | Voice mode, wake word | Installed and used on the PC | Python 3.11 venv | Default TTS cloud | Own tool loop | CLI only | ~0% of PJ voice code (§3) | Medium | REJECT |
| pipecat-ai/pipecat | BSD-2 | active 2026-09 | Python | Voice pipeline | Large test suite, releases | Many optional services | Mostly cloud services by default | None for tools | Frame processors | Orchestration only; offset by re-hosting | Medium | REJECT |
| livekit/agents | Apache-2.0 + LiveKit Model License | active | Python | Voice agent | Releases, tests | LiveKit server/rooms | Room-based | Own tool executor | Agent session | Low | Medium | REJECT |
| pipecat-ai/smart-turn v3.2 | BSD-2 | — | ONNX model | Semantic end of turn | Vendored and measured (74 ms median) | 8.68 MB CPU model | Local | n/a | `voice_engine/turn.py` | Already reused | Low | EXTRACT (done); wiring DEFER |
| RapidAI/RapidOCR | Apache-2.0 (code and weights) | 2026-10-03 | Python | OCR with word boxes and scores | Measured here: 0.981 on fixtures | onnxruntime (base), opencv-python, shapely, pyclipper, omegaconf; CPU | Local with bundled models; non-default models download from modelscope.cn | Library call | `uitext._rapidocr_words` | Replaces the native Tesseract need | Low–medium | USE DIRECTLY |
| tesseract-ocr + pytesseract | Apache-2.0 | 2026-09-28 / 2025-02-17 | C++ / Python | OCR | Used before this pass | Native binary | Local | Library call | `uitext._tesseract_words` | — | Medium (stale binding) | Keep as fallback |
| pywinrt (Windows.Media.Ocr) | MIT | 2026-09-13 | Python | OCR without confidence | Type stubs | Windows only | Local | — | — | — | Low | DEFER |
| openai/codex execpolicy | Apache-2.0 | 2026-10-02 | Rust | Command policy | Tests in repo | Rust | Local | Rule engine | None for Python | Ideas only | — | ADAPT (later) |
| anthropic-experimental/sandbox-runtime | Apache-2.0 | 2026-10-03 | TypeScript | OS sandbox | README, releases | Node; elevated install | Local | Dedicated user, egress filter | Subprocess wrapper | — | Medium | DEFER |
| OmniParser, UI-TARS-desktop, Agent-S, open-interpreter, screenpipe, TEN, PaddleOCR | see §2 | — | — | — | — | — | — | — | — | — | — | REJECT |

**Licensing, supply chain and security.**
- RapidOCR's own code and the bundled default weights are Apache-2.0.
- Its dependencies (opencv-python Apache-2.0, shapely BSD-3, pyclipper MIT,
  omegaconf BSD-3, colorlog MIT, antlr4 runtime BSD-3) are permissive.
- It ran no install script beyond the wheels.
- Its only network behaviour is downloading a non-default model; PJ uses only
  the bundled ones.
- It has no telemetry (read in `rapidocr/utils/download_file.py` and
  `main.py`).
- No other repository was installed or executed; they were read only.

## 6. Reuse matrix and compression

| Capability | Candidate | Evidence | Reuse approach | Estimated code eliminated | Risks | Decision |
| --- | --- | --- | --- | ---: | --- | --- |
| Voice runtime | Hermes Voice | §3 | — | 0 lines (nothing it does is missing in PJ) | Cloud TTS default, agent coupling | REJECT |
| Voice runtime | pipecat | F3 | Replace the pipeline | Not net-positive: re-hosting needed (inferred) | Two event models | REJECT |
| End of turn | Smart Turn v3.2 | F7 | Already vendored; wire into the classic pipeline | 0 (adds about 50 lines) | German accuracy unverified | DEFER |
| OCR | RapidOCR | F9, F10 | Library behind `uitext` | Removes the native-binary requirement; the Tesseract parsing stays as fallback | 2 s per image on a container CPU | ADOPT |
| Computer use | Agent-S, UI-TARS, OmniParser | F14–F16 | — | 0 | `exec` of model code; licences; VRAM | REJECT |
| Command safety | codex execpolicy | F19 | Copy the rule-plus-example design | 0 now | Rust only | ADAPT later |
| Cloud vision | NVIDIA NIM | F24 | — | 0 | Terms forbid personal data | REJECT |

### Architecture compression opportunities

```text
Hermes Voice Mode
-> replaces/absorbs: no PJ module (every capability is already present, §3)
-> preserves Brain abstraction: no (drives its own agent)
-> requires: an adapter replacing Hermes's chat()/process_loop
-> risk: cloud TTS default, agent coupling
-> decision: REJECT

RapidOCR
-> replaces/absorbs: the requirement for a native Tesseract install
-> preserves Brain abstraction: yes (screen context only)
-> requires: a ~60-line adapter to the existing word/line model
-> risk: CPU time per capture; optional install
-> decision: ADOPT

Smart Turn v3.2 (already vendored)
-> replaces/absorbs: the fixed 1.5 s Silero endpoint wait in the classic pipeline
-> preserves Brain abstraction: yes
-> requires: wiring plus threshold tuning on on-device recordings
-> risk: early cut-offs in German (unverified)
-> decision: DEFER
```

## 7. Step 3.7 Flash

Measured on the PC on 2026-10-03 at about 20:05 UTC. The route was Nous's free
endpoint through the owner's gateway (`C:\paperclip\tools\nous-free-proxy.mjs`
on `127.0.0.1:11436`). The gateway was started for the test and stopped again.

| Test | Result |
| --- | --- |
| 5 streamed short prompts | First token 2.40 / 1.88 / 3.00 / 2.37 / 1.89 s (mean about 2.3 s); total 2.2–4.1 s; 137–235 tok/s; all answers correct |
| Reasoning | 164–830 reasoning characters stream before the content; PJ reads only `delta.content` (`_openai_base.py`), so reasoning is never spoken |
| Tool call (one dummy `get_weather` tool) | First attempt HTTP 429; then HTTP 200 in 2.91 s with a valid `get_weather {"city": "Paris"}` |
| Image input (synthetic PNG, no screen capture) | Three 429s, then HTTP 200 in 4.01 s; read "INVOICE 4721 PAID" exactly |
| Rate limits | 4 of 11 requests got HTTP 429 with `retry_after` 3–33 s. The 429 body suggests paid alternatives, which Jarvis must never follow. |
| Reasoning-effort variants (about 20:10–20:25 UTC) | **Blocked by rate limiting.** Variants "no extra params" and `reasoning_effort: "low"`: all 3 prompts each returned HTTP 429 after 4 retries that honoured `retry_after`. 24 consecutive attempts failed, so the run was stopped before the other variants. Whether the API honours reasoning controls is unknown. |

**Feasibility.**
- Local use is impossible: the smallest GGUF is about 102 GB against 8 GB of
  VRAM and 16 GB of RAM (F21).
- NVIDIA's free endpoint for it is deprecated (F24 source).
- OpenRouter's `:free` variant is limited to 20 requests per minute and
  50 per day.
- Nous still lists it as free with no published expiry (unverified how long).

**Role.** Step 3.7 Flash is the right **fast tier**: tools work, latency is
acceptable for a spoken first answer, and it is free. It is **cloud**. The
loopback URL does not make it local, so it is not marked `local` and, by
default, receives no screenshots (§9.1). Its rate limits make the second tier
necessary: within one hour the same free route went from 7 of 11 requests
answered to 24 consecutive 429s. It must never be the only brain on the voice
path, and failover to the next tier has to be deterministic (it is: the policy
chain always has the other tier as the one bounded fallback). It does not replace Qwen, because it cannot run on this machine.

## 8. Qwen, local vision and NIM

### Qwen 3.6 35B-A3B quantisation

Facts from the PC (read-only, nothing loaded or downloaded):
- `C:\models\Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf` is present: 22,360,456,160
  bytes (20.82 GiB), downloaded on 2026-10-03 from a third-party mirror. Its
  SHA-256 matches the official unsloth LFS object
  (`707a55a8…1043f4450`), so the file is authentic.
- The runtime is llama.cpp b11146 (CUDA 12.4); no mmproj file exists on the
  disk, so this model cannot take images locally yet.
- An existing baseline on the PC (`C:\models\qwen65k-llamacpp-baseline.json`,
  written by another session) ran this file at 65,536 context. Both cases
  returned empty output after about 30 s; free RAM fell to 0.08–0.12 GB; the
  GPU held only about 1.3 GB. That configuration is not usable and sits in
  the hard-hang zone the brief warns about.
- With Jarvis, Hermes and Paperclip running and Qwen not loaded: about 3.9 GB
  of 15.2 GB RAM available, 6.9 GB of 8 GB VRAM free.

Quant options:
- The official alternatives are UD-IQ4_NL_XL (18.16 GB) and UD-Q4_K_S
  (19.46 GB) against the current 20.82 GB. All three exceed 8 GB VRAM plus
  the about 3.9 GB of RAM that is free while normal apps run, so each would
  page from disk.
- Published partial-offload results on other GPUs are 15–39 tok/s. They are
  not this machine.
- A benchmark of any quant needs the owner to close other apps first, with a
  small context (4–16k) and experts on the CPU. Running it unattended risks
  the documented hard hang.

**Decision: DEFER** the quant change, and do not mark the Qwen tier `local`
for vision until an mmproj is installed and measured. The existing setup is
not yet a stable baseline, so no quant can be called better.

### Local vision

**Decision: DEFER a separate VLM.**
- Qwen3.6 has native vision through a 0.84 GB mmproj (F16). Once that file is
  installed and the deep tier runs stably, a deep tier marked `local = true`
  is the local vision path, and it needs no second model.
- No mmproj is on the PC today, so there is no local vision now (PC check).
- Windows OCR language packs on the PC are en-US and en-GB only.
- Qwen3-VL-2B/4B would fit on paper (1.1–2.5 GB plus mmproj, OCRBench
  858/881). However, it would compete with STT, TTS and any local deep model
  for 8 GB of VRAM, and nothing was measured on the device.
- Local OCR (RapidOCR) covers text on screen.

### NVIDIA NIM: the six conditions

| # | Condition | Result |
| --- | --- | --- |
| 1 | A current NIM model does image understanding | Met: free endpoints listed for llama-3.2-90b-vision-instruct, paligemma and others (fetched) |
| 2 | Free access is sufficient for limited fallback | Not met: evaluation-only terms ("not in production"), unpublished revocable rate limits |
| 3 | Fixes a real failure local OCR/vision cannot handle | Not shown: local OCR reads the fixtures at 0.981, and no failing local case is recorded |
| 4 | Latency acceptable for voice | Unverified: nvidia.com is blocked from the container and no key is configured |
| 5 | Can be isolated behind explicit consent | Possible (`allow_cloud_vision`) |
| 6 | Does not weaken privacy | Not met: the trial terms forbid uploading personal information, and desktop screenshots contain it |

**Decision: REJECT NIM.** No adapter, configuration or placeholder was added.

## 9. Build-vs-reuse checks and what was implemented

### 9.1 Images stay on local targets unless the user allows cloud vision

```text
BUILD VS REUSE CHECK
Capability: keep screenshots, camera frames and dropped images off cloud models by default
Existing PJ implementation: route policy chain + _lead_vision_chain; no locality notion
External candidates: none (this is a policy over PJ's own provider chain)
Best reusable candidate: PJ's own decide_route/filter_denied
What can be reused: the policy module, RouteDecision, BrainRouteSelected
What must remain custom: which targets are local (owner configuration)
Integration cost: one pure function plus one branch in _generate
Security impact: positive (no target from outside the configured tiers)
Privacy impact: positive (no silent cloud transmission of images)
Performance impact: none
Maintenance impact: small
Decision: ADOPT (implemented)
```

Changes:
- `RouteTargetConfig.local`, `BrainRoutePolicyConfig.allow_cloud_vision`
  (default false).
- `route_policy.media_chain` and `is_local_target`.
- In `BrainManager._generate`, an image turn under the policy keeps only local
  targets unless consent is set.
- With no local target, the turn answers in the turn's language that the
  image stays on the device, publishes a `blocked` route event, and calls no
  model.
- `_hoist_tool_model` no longer pulls a provider from outside the policy chain.
- A tool screenshot taken during a turn is shown to the model only when that
  model may receive images (`tool_images` on `BrainDispatcher` and
  `ToolUseLoop`). Otherwise the model is told the image stayed on the device.
- Computer Use, for all three engines, selects through
  `ComputerUsePlannerSelector`. It now skips targets that may not receive
  screenshots, and its last resort stays inside the policy's tiers. If the
  policy cannot be read, it fails closed.

Tests:
- `test_route_policy.py`: media_chain, is_local_target.
- `test_route_policy_media_trace.py`:
  - an image goes to the local target only;
  - blocked when there is no local target;
  - consent lets the cloud target take it;
  - text turns are unaffected;
  - hoist confinement;
  - `tool_images` per target.
- `test_tool_use_loop_image_feedback.py`: a tool screenshot is kept or shown.
- `tests/unit/cu/test_brain_call_media_privacy.py`:
  - Computer Use goes local only;
  - no model when nothing is local;
  - consent;
  - no change without a policy;
  - last-resort confinement.

### 9.2 One trace id per turn

```text
BUILD VS REUSE CHECK
Capability: one correlation id across voice, route, tools and TTS
Existing PJ implementation: LatencyTracker.trace_id per voice turn; Brain generate(trace_id=); OpenTelemetry API present
External candidates: OpenTelemetry (already a dependency)
Best reusable candidate: the existing trace_id parameter
What can be reused: all of it; only the hand-off was missing
What must remain custom: nothing new
Integration cost: two call sites plus one dispatch argument
Security/privacy impact: none
Performance impact: none
Maintenance impact: the TypeError signature cascade moved into two small helpers
Decision: ADOPT (implemented)
```

Changes:
- `SpeechPipeline._open_brain_stream` and `_open_brain_completion` pass the
  tracker's `trace_id` to the Brain. Adapters that do not take it fall back
  without losing `allow_voice_confirm` or attachments.
- `BrainManager` passes the turn's own id (`trace_uuid`) to the dispatcher, so
  tool events carry it even when the caller gave none.

Tests:
- `test_turn_taking.py`: the stream and the completion carry the id; an older
  adapter keeps confirm and attachments.
- `test_route_policy_media_trace.py`: the route event and the dispatch share
  the id, with and without a caller id.

### 9.3 RapidOCR as the first OCR engine

```text
BUILD VS REUSE CHECK
Capability: local OCR with word boxes and confidence (for masking and pixel redaction)
Existing PJ implementation: pytesseract adapter in uitext (needs a native binary not on the PC)
External candidates: RapidOCR, Tesseract, Windows.Media.Ocr, PaddleOCR, OmniParser
Best reusable candidate: RapidOCR (F9, F10)
What can be reused: the engine and its bundled models, unchanged
What must remain custom: mapping to PJ's word/line model, score scale, masking threshold
Integration cost: ~60 lines adapter; the shared word assembly is the old Tesseract code, moved
Security impact: none (library call, no network with bundled models)
Privacy impact: positive (local; low-score lines kept for redaction via text_score=0)
Performance impact: ~2.1 s per small image on a 4-vCPU container; device unmeasured
Maintenance impact: optional dependency, probed like pytesseract
Decision: ADOPT (implemented)
```

Changes:
- `uitext._rapidocr_words`, `_tesseract_words` and `_supplement_from_words`
  share one assembly and masking path.
- `ocr_engine_status` is the single availability probe; the Settings route
  uses it.
- `ocr_supplement` now uses the same engines.
- The install stays optional (`pip install rapidocr`). It is not added to
  `pyproject.toml`, because `uv lock` could not refresh from the container:
  the project's custom package index is unreachable from it.

Tests: `test_uitext.py` (RapidOCR preferred, score scale, low-score masking
with full redaction geometry, engine status) and `test_ocr_accuracy_gate.py`
(now runs with either engine).

### 9.4 Claude explicit-only and localized recovery

```text
BUILD VS REUSE CHECK
Capability: escalation only on explicit request; recovery in the turn's language
Existing PJ implementation: wants_escalation (explicit), on_deep_failure (automatic), English RECOVERY_MESSAGES
External candidates: none
Decision: ADOPT (implemented): on_deep_failure removed (old configs still load; the key is ignored); RECOVERY_MESSAGES per language (en/de/es) via recovery_message()
```

Tests: `test_route_policy_manager.py` (recovery in the turn's language) and
`test_route_policy_media_trace.py` (a failed deep turn calls no delegate).

### Rejected or deferred after code inspection

- Signature-based duplicate tool suppression: REJECT (F33).
- New status states or UI: REJECT. Existing events already cover listening,
  thinking, approval, executing, error and speaking (`TurnTakingState`,
  `BrainTurnStarted`, `ActionApprovalRequired`, `ActionExecuted`,
  `ErrorOccurred`).
- `retry_after`-based cooldown: DEFER (F34).

## 10. Live validation log

| Date (UTC) | Hardware | Model / service | Configuration | Test | Result | Latency | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-10-03 ~20:05 | Owner's PC | Step 3.7 Flash via Nous free | Loopback gateway 127.0.0.1:11436 | 5 prompts, tool call, image | PASS (all correct) | First token mean ~2.3 s | 4/11 requests rate-limited; recovered after retry |
| 2026-10-03 | Owner's PC | Paperclip 127.0.0.1:3100, agent `dan` (free) | Fork `5d4403b1`, Python 3.11.15, httpx 0.28.1 | Delegation smoke "PONG" | PASS (JAR-16 done) | 46.1 s round trip | Paperclip down after a reboot gave typed UNAVAILABLE in 2.3 s; its scheduled task has only a time trigger |
| 2026-10-03 | Owner's PC | Hermes Agent 0.21.3 | Installed | Source reading | Done | — | §3 |
| 2026-10-03 | Linux container, 4 vCPU | RapidOCR 3.9.2, PP-OCRv6 small, onnxruntime CPU | Bundled models | 5 rendered UI fixtures | 0.981 char accuracy (target 0.95) | ~2.1 s median per image | Synthetic text, not real screens |
| 2026-10-04 | Owner's PC | RapidOCR 3.9.2, onnxruntime 1.30 CPU, scratch venv `%USERPROFILE%\jarvis-ocr-venv` | Fork at `1e56668f`, bundled models only (network blocked during init) | Gate fixtures, screen-context suite, one full-screen capture kept in memory | PASS: 0.981 char accuracy; 11 + 320 tests pass; full screen 1920x1080 gave 101 lines, 400 words, mean confidence 95.9, 13 words masked | Median 865 ms, p90 946 ms per small image; 2.98 s full screen | Init 1.47 s; Tesseract fallback not installed on the PC (unit-tested only) |
| 2026-10-04 | Owner's PC | Microphone Array (Realtek) | WDM-KS vs WASAPI shared vs MME, 2 s captures | Root cause of the silence | Device configuration (F37) | — | No setting changed |
| — | Owner's PC | Voice barge-in | — | — | HARDWARE-BLOCKED | — | Needs the microphone fix (status doc, manual steps) |

No paid API was called. No Claude agent or model was used. No model was
downloaded except RapidOCR's wheel (27 MB, models bundled), which went into
the container's virtualenv only.

## 11. Sources

- https://github.com/NousResearch/hermes-agent (installed copy on the PC)
- https://github.com/pipecat-ai/pipecat · https://github.com/pipecat-ai/smart-turn
- https://github.com/livekit/agents · https://github.com/KoljaB/RealtimeSTT ·
  https://github.com/KoljaB/RealtimeTTS ·
  https://github.com/TEN-framework/ten-framework
- https://github.com/RapidAI/RapidOCR · https://pypi.org/project/rapidocr/ ·
  https://github.com/PaddlePaddle/PaddleOCR ·
  https://github.com/tesseract-ocr/tesseract · https://github.com/pywinrt/pywinrt
- https://github.com/microsoft/OmniParser ·
  https://github.com/bytedance/UI-TARS-desktop ·
  https://github.com/simular-ai/Agent-S ·
  https://github.com/OpenInterpreter/open-interpreter ·
  https://github.com/mediar-ai/screenpipe
- https://github.com/openai/codex (execpolicy, windows-sandbox) ·
  https://github.com/anthropic-experimental/sandbox-runtime
- https://huggingface.co/stepfun-ai/Step-3.7-Flash ·
  https://static.stepfun.com/blog/step-3.7-flash/ ·
  https://openrouter.ai/stepfun/step-3.7-flash:free ·
  https://openrouter.ai/docs/api/reference/limits ·
  https://portal.nousresearch.com/models
- https://huggingface.co/Qwen/Qwen3.6-35B-A3B ·
  https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF ·
  https://github.com/ggml-org/llama.cpp/discussions/21112
- https://build.nvidia.com · https://docs.api.nvidia.com/nim/docs/product · NVIDIA
  API Trial Terms of Service (assets.ngc.nvidia.com)
- https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF ·
  https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF ·
  https://arxiv.org/pdf/2511.21631
- https://github.com/thecodacus/dexter · https://github.com/zubair-trabzada ·
  https://moderncreator.app/2026-09-02-zubair-trabzada-ai-workshop-i-built-a-real-life-jarvis-with-claude-fable-5-1
