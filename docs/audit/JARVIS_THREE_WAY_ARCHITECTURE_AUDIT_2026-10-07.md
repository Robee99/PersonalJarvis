# Three-way architecture audit (2026-10-07)

PersonalJarvis (`Robee99/PersonalJarvis`, branch `jarvis/local-qwen-build`,
commit `1d0c4415`) against `EliseyRotar/jarvis-ai` (commit `7ad5c6d`) and
official Hermes Agent (v0.21.5, source commit `33c9b1d7`). All three were read
from source; file:line evidence for each claim is in the three working
reports summarised here. Authority: ADR-0042 (Hermes is the single brain).

## The verdict in one paragraph

Hermes already provides every agent capability this product needs (tools,
skills, MCP, plugins, memory, sessions, browser, computer use, vision,
delegation, code, cron, approvals, model routing and free fallbacks).
jarvis-ai shows that a thin voice shell over Hermes is the right shape, but its
code is thin and buggy and is a source of ideas, not of code. PersonalJarvis
has the strongest shell parts (voice pipeline, approvals, safety, UI) but still
carries a dozen competing paths into a model, and its defaults never reach
Hermes. **The work is subtraction and wiring, not new features.**

## What each one does

| Capability | PersonalJarvis | jarvis-ai | Official Hermes | Best implementation | Action |
|---|---|---|---|---|---|
| Voice loop | `SpeechPipeline` (`jarvis/speech/pipeline.py`, 18.6k lines): wake, VAD, STT, sentence TTS, barge-in, confirm-by-voice | openwakeword + batch faster-whisper + edge-tts, serialized, no VAD on Windows, barge-in leaves the Hermes run speaking | CLI voice mode (wake word, VAD, streaming TTS) but **not exposed over the API server** (`audio_api: false`) | PersonalJarvis pipeline | KEEP; make it the only voice path |
| STT | faster-whisper, Nemotron (sherpa), cloud options | faster-whisper `medium.en`, VAD off, no hallucination guard | faster-whisper with Silero VAD (CLI only) | PersonalJarvis | KEEP; fail closed when a local engine's files are missing |
| TTS | local and cloud engines, sentence streaming | edge-tts per sentence, drops short sentences, speaks reasoning tags | edge, Piper, KittenTTS (CLI only) | PersonalJarvis | KEEP |
| Wake word | own detector | openwakeword `hey_jarvis` | openwakeword / Porcupine (CLI only) | PersonalJarvis | KEEP |
| Barge-in | stops TTS and calls `/v1/runs/{id}/stop` (`hermes.py` `_close`) | stop button stops the run; wake word only silences TTS | `/v1/runs/{id}/stop`, `/steer` | PersonalJarvis + Hermes `/steer` | KEEP; TAKE `/steer` for typed corrections mid-run |
| Realtime voice brains | Gemini Live, Vertex, OpenAI realtime/live/subscription, local voice engine with its own LLM (26k + 5.5k + 4.4k lines); **`voice.mode` defaults to `realtime`** | none | GPT-Live (paid) | none | DELETE from the Hermes product (COMPATIBILITY only for non-Hermes installs) |
| Text chat | front-page agent chat: 19 seats, only the `hermes` seat reaches Hermes; 9 API seats and 9 vendor-CLI seats each answer as Jarvis | one WebSocket, same Hermes session as voice | Sessions API, runs API | One seat: Hermes | ADAPT: the Jarvis surface offers Hermes only |
| Same session voice ↔ text | both reach `jarvis-main` only when Hermes is picked in two separate places (`brain.primary` and the chat pick), through two `HermesBrain` instances | yes (one global list + one Hermes session) | `session_id` on every run | Hermes session `jarvis-main`, one Jarvis instance | ADAPT: one switch, one instance |
| Fallback | `manager.py` legacy chain adds `deep_brain`, router fallbacks and claude/gemini/openrouter/openai/grok/nvidia when keys exist | none | `fallback_providers`, free-only aux lane | Hermes | DELETE the Jarvis chain while Hermes is the brain |
| Background LLM use | wiki curation, learning review, `/goal` verifier, scheduled tasks and provider tests can run **inside `jarvis-main`** or fall to other providers | warmup sends "ok" turns into real sessions | separate sessions are free | separate Hermes sessions | ADAPT: never the user's session |
| Instant acknowledgement | flash LLM writes ack and readback lines (gemini/grok/openai/ollama) | canned "On it." phrases | none | canned phrases | ADAPT: canned only while Hermes is the brain (no second model speaking) |
| Memory | wiki vault, FTS, watcher, importer, secret guard; reachable from Hermes via Jarvis MCP | mem0 + qdrant + MCP, overlaps Hermes memory | built-in memory, session search | Hermes memory + Jarvis wiki as an MCP knowledge source | KEEP wiki as knowledge (not a second memory brain) |
| MCP | Jarvis `mcp.json` (3 servers) + Jarvis outward MCP | none of its own besides mem0 | `mcp_servers`, 65-entry catalog, reconcile loop | Hermes | KEEP Jarvis outward MCP only to serve the wiki; agent MCP is Hermes's |
| Skills | 30 native skills (Jarvis-only path, stands down under Hermes) | none | 58 bundled + 152 optional + hub | Hermes | Armory shows Hermes's (done, `36091771`) |
| Plugins | Jarvis marketplace apps | none | `hermes plugins` | Hermes | Armory controls Hermes's (done) |
| Browser | legacy Jarvis browser tools (unused under Hermes) | Playwright MCP | agent-browser Chromium, Lightpanda, CDP, Camofox, keyless free web search ring | Hermes | Hermes |
| Computer use | Jarvis CU (dead under Hermes) + look-only guard | none | `computer_use` via cua-driver (UIA on Windows), approval per action; **off for `api_server` by default** | Hermes + Jarvis look-only guard | ADAPT: free-voice enables it for `api_server` when the driver is present |
| Vision | images forwarded in the last run message (`60560281`) | screenshot on any message containing "screen" | image parts; `vision_analyze` for non-vision models; local VLM via `auxiliary.vision` | Hermes | KEEP |
| Delegation | watches the session transcript for results | none | `delegate_task`, `subagent.*` events | Hermes | KEEP |
| Code execution | (Jarvis tools, unused under Hermes) | none | `execute_code` | Hermes | Hermes |
| Approvals | spoken question, yes/no/unclear, run resolved | **none** (agent runs unconfirmed) | `approval.request` + `/approval`, smart mode | PersonalJarvis presentation + Hermes gate | KEEP |
| Society agents | 22k lines; a hermes seat writes into `jarvis-main` | none | delegation, profiles | Hermes | COMPATIBILITY; give any hermes seat its own session |
| Missions / Agentic IDE | separate surfaces (ADR exception) | none | `hermes -z` | — | KEEP as separate surfaces |
| UI | many surfaces (home chat, legacy chat, memory orb, armory, HUD, decks) | one HUD | dashboard on :9119 | PersonalJarvis, pruned | pre-mortem (separate doc) |
| Windows | installer, autostart, elevation handling | LOCAL SYSTEM service (no desktop/audio in session 0) | desktop app | PersonalJarvis | KEEP |
| Latency | per-stage timings exist in the voice pipeline | total only | streaming, prompt caching, `service_tier`; fresh agent per request | PersonalJarvis + Hermes config | ADAPT: time-to-first-audio budget, fast aux models |

## Defects found in the references (do not copy)

* jarvis-ai: no VAD on Windows; no echo cancellation; no STT hallucination
  guard; wake word does not stop the Hermes run; TTS drops sentences under 12
  characters and speaks `<cosmo:think>` reasoning; "reset" reuses the same
  session id; warmup writes "ok" turns into real sessions; no approvals; runs as
  LOCAL SYSTEM.
* Hermes v0.21.5: `GET /v1/skills` answers 500 (`api_server.py:2957` passes
  `include_editorial`, which `_find_all_skills` does not accept); `/v1/runs`
  gets no reasoning or status callbacks (compaction and fallback notices never
  reach Jarvis); `computer_use`, `clarify` and `text_to_speech` are not in the
  default `api_server` toolset; a fresh `AIAgent` is built for every request.

## Ordered plan

1. **One brain, one session** (done in this change set):
   * the Jarvis chat surface offers only the Hermes seat while Hermes is the
     brain, and moves older chats onto it;
   * voice and chat join the same Hermes session and share its pending
     approval;
   * no Jarvis fallback chain behind Hermes;
   * background work (wiki curation, learning review, `/goal` verification,
     scheduled tasks, provider tests) runs in its own Hermes session, never in
     `jarvis-main`;
   * instant acknowledgements are canned while Hermes is the brain.
2. **Hermes setup by free-voice**: computer use installed and enabled for
   `api_server` (done); fast free models for `auxiliary.compression` and
   `auxiliary.approval`; context cap (done).
3. **Acceptance run on the PC** (`jarvis system acceptance`, done; matrix in
   `JARVIS_FINAL_ACCEPTANCE_MATRIX_2026-10-07.md`): a command that drives the Windows
   scenarios through the running app and writes a PASS/FAIL scorecard with
   per-stage latency, so a release is judged by evidence.
4. **Delete** what step 1 leaves unreachable in the Hermes product (realtime
   voice brains, CLI seats on the Jarvis surface, the legacy fallback chain),
   with the import and test search ADR-0042 requires before each deletion.
5. UI pre-mortem fixes (Memory Orb) once the runtime is single-brain.
