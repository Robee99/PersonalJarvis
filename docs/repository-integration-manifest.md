# JARVIS Repository and Model Integration Manifest

**Reconciled:** 2026-10-17  
**Canonical application:** `Robee99/PersonalJarvis` (PersonalJarvis/Dodaios fork)  
**Upstream:** `Dodaios/Dodaios`
**Current implementation branch:** `codex-gemma-mcp-fix-20261014`; latest source milestone `7bd27f6da` refreshes the MCP token store when another worker changes its file. Later documentation commits record Windows acceptance evidence. Earlier milestones include `8e6e98a5c` (World Intelligence section and loopback embed), `f2cbe7e` in the GEV checkout (safe embed framing), `09d715bef` (local context fit), `08504c6a0` (local voice acknowledgment adapter), and `67addbf0b` (Windows evidence).

This manifest records repository identity, local clone evidence, intended role, and delivery state. A local HEAD is an inventory snapshot, not a claim that it matches the current upstream tip. No broad model tournament or framework migration is authorized by this manifest.

## Delivery status meanings

Track these independently for every component:

- **CLONED** — source exists in an isolated local checkout.
- **ADAPTED** — compatible code or configuration has been adapted into PersonalJarvis.
- **TESTED** — the stated automated or manual test passed; record its scope.
- **DEPLOYED** — enabled in a named PersonalJarvis runtime/build.
- **VERIFIED IN THE LIVE WINDOWS APPLICATION** — the integrated path was exercised successfully in the user's running Windows application.

One state never implies another. In particular, source presence, pairing, unit tests, and a successful non-live preview do not mean the live application is verified.

For every row, keep all five states distinct. If a row does not explicitly establish a state, record it as **not established** rather than inferring it from another state.

## Main application and existing local clones

Local clone inventory was read from `Documents/Codex/2026-10-14/external-repos`. Hashes are abbreviated local HEADs observed during inventory; fetch before calling any one of them the latest upstream revision.

| Repository | Local HEAD | License observed | Role and status |
|---|---:|---|---|
| [Robee99/PersonalJarvis](https://github.com/Robee99/PersonalJarvis) | source milestone `7bd27f6da` on `codex-gemma-mcp-fix-20261014` | Main project | Authoritative application. Branch also contains later manifest-evidence commits. Source was writable for this implementation; the user's installed app remains untouched. Preserve all unrelated dirty files and user data. |
| [Dodaios/Dodaios](https://github.com/Dodaios/Dodaios) | `a9e449d` | Apache-2.0 | Upstream architecture/reference; never replace the user's fork with this checkout. **CLONED**. |
| [topoteretes/cognee](https://github.com/topoteretes/cognee) | `f154b32` | Apache-2.0 | Memory engine. PersonalJarvis has isolated adapter/worker code. **CLONED: yes; ADAPTED: yes; TESTED: synthetic lifecycle passes; real Cognee 1.6.4 Windows lifecycle failed on three isolated roots, including once with Cognee's LanceDB subprocess proxy enabled. `cognify` cannot commit `TextSummary_text` versions because LanceDB's local file-copy operation fails; latest run failed after 84 s. DEPLOYED: no; LIVE WINDOWS: no.** Cognee 1.6.4 is installed in isolated `C:\models\jarvis-cognee-runtime-final`; no real conversation source is registered. Keep disabled until a supported Windows storage path passes the full persistence/update/delete acceptance. |
| [microsoft/graphrag](https://github.com/microsoft/graphrag) | `5faaaf4` | MIT | Optional multi-document knowledge graph retrieval. **CLONED: yes; ADAPTED: yes; TESTED: unit tests (3 passed, 1 skipped) plus Windows local integration acceptance (1 passed): three synthetic source records indexed through local Qwen llama.cpp and CPU FastEmbed; GraphRAG produced entities, relationships, community reports, text units and SQLite vectors; semantic and global retrieval returned source provenance.** No paid APIs used. The feature remains disabled and is not deployed/live-verified. Windows worker emits a successful result then exits with `3221225480` during native Python shutdown; the adapter accepts only valid positive result JSON and logs this warning. The isolated SQLite vector store avoids LanceDB's Windows manifest access-denied failure. |
| [bilawalsidhu/gods-eye-view](https://github.com/bilawalsidhu/gods-eye-view) | `6571886` on `codex/gev-v021-hermes-integration` (incident layer `b033ca4` + `6571886` on top of `f2cbe7e`; upstream base `aa16b7c`); existing changes still staged/unstaged | MIT (datasets may have separate terms) | Globe/Command Center integration. **CLONED: yes; ADAPTED: a JARVIS World Intelligence view embeds the loopback GEV runtime; TESTED: previous MCP call returned a view URL, GEV CSP tests pass, and isolated Windows Chromium integration confirmed an embedded Cesium canvas; DEPLOYED: no; LIVE WINDOWS: not established for the user's installed app.** The already-running GEV server on `127.0.0.1:4173` still has its previous deny-frame policy; restarting that user process and rebuilding/restarting JARVIS are required before the new integration is available there. Keep all pre-existing staged and unstaged GEV files untouched. **World incidents (2026-10-19):** GEV draws the World screen's incidents through the `events@1` postMessage contract with two-way selection; see `docs/world-intelligence.md` and `docs/world-intelligence/gev-incident-layer.patch`. Committed in the user's checkout with `git commit --only`, leaving the other staged and unstaged files as they were.
| [szczyglis-dev/py-gpt](https://github.com/szczyglis-dev/py-gpt) | `a6766d0` | MIT | Agent, memory, voice, RAG and computer-use donor. **CLONED: yes; ADAPTED/TESTED/DEPLOYED/LIVE: no verified integration.** Reuse individual compatible modules only. |
| [tinyhumansai/openhuman](https://github.com/tinyhumansai/openhuman) | `6313ad0` | GPL-3.0 | Orchestration/memory research donor. **CLONED: yes; other states: no verified integration.** Preserve copyleft obligations; select code only for a specific demonstrable gain. |
| [existence-master/Sentient](https://github.com/existence-master/Sentient) | `8e6bb0a` | AGPL-3.0 | Voice-interruption and companion behavior reference. **CLONED: yes; other states: no verified integration.** Avoid a second runtime; review AGPL before code reuse. |
| [VyomKulshrestha/Heliox-OS](https://github.com/VyomKulshrestha/Heliox-OS) | `79af30e` | MIT | Windows automation, approvals and durable-task reference. **CLONED: yes; other states: no verified integration.** Audit model/plugin asset licenses separately. |
| [antoinezambelli/forge](https://github.com/antoinezambelli/forge) | `a6c9d3c` | MIT | Agent/tool donor. **CLONED: yes; other states: no verified integration.** Existing checkout has Git LFS errors and its Python baseline is newer than the app runtime; defer dependency adoption. |
| [egroup-labs/kept](https://github.com/egroup-labs/kept) | `c81d59f` | MIT | **ADAPTED 2026-10-26:** extension bundled in `jarvis/assets/kept-extension` (auto-sync on); Jarvis serves the Kept app API on 127.0.0.1:18241 (`jarvis/memory/wiki/kept_bridge.py`) and writes chats into memory. No Kept desktop app. |
| [Dix01/JARVIS](https://github.com/Dix01/JARVIS) | `dff115c` | MIT | **Selective GUI code donor**, not screenshot-only reference. **CLONED: yes.** Port compatible Command Center/Mission Control components when they improve the existing UI; no port/test/deployment/live verification recorded here. |
| [mmAbdelhay/JARVIS](https://github.com/mmAbdelhay/jarvis) | `5818d1f` | MIT | **Selective GUI code donor**, not screenshot-only reference. **CLONED: yes.** Evaluate and port compatible Command Center/Mission Control components; no port/test/deployment/live verification recorded here. |
| [aligh993/JARVIS](https://github.com/aligh993/JARVIS) | Not in observed local clone inventory | Not verified | Separate JARVIS project mentioned in the implementation prompt. **CLONED/ADAPTED/TESTED/DEPLOYED/LIVE: no evidence.** Keep as a selective donor only if a concrete compatible UI component improves the existing app. |
| [LeiQingliang/J.A.R.V.I.S.-Holographic-Interface](https://github.com/LeiQingliang/J.A.R.V.I.S.-Holographic-Interface) | Not in observed local clone inventory | Not verified | Holographic UI reference project. **CLONED/ADAPTED/TESTED/DEPLOYED/LIVE: no evidence.** Don’t replace the current Command Center or add a separate dashboard. |
| [Divyansh900/PyCode-Agent](https://github.com/Divyansh900/PyCode-Agent) | `0569b68` | GPL-2.0 | Disambiguated PyCode candidate, not the intended PyGPT. **CLONED: yes; reject/defer** absent a specific need and license-compatible module. |
| [spkc83/pycode-mcp-server](https://github.com/spkc83/pycode-mcp-server) | `4c26ac4` | MIT | Separate code-execution MCP, not PyGPT. **CLONED: yes; not enabled.** High local execution privilege makes it unsuitable as a default. |

## Current Windows runtime evidence — 2026-10-17

- **JARVIS backend:** `GET http://127.0.0.1:47821/api/health` returned `ok`, version `2.7.1`.
- **Local model route:** the live `local-openai` catalog reported `qwen3-vl-8b` as the current model; `GET /v1/models` on `127.0.0.1:11440` exposed that model. JARVIS's real local provider test returned `ok` in `548.8 ms`. The separate Ollama catalog returned no installed models. This does not establish that Gemma or UI Vision 2 is reachable.
- **MCP:** the existing runtime reports 26 agent-surface tools and one active restricted “Hermes local Gemma” token. Unit/contract coverage after commit `7bd27f6da` passed 55 tests. A separate Windows development instance started from that commit passed Streamable HTTP initialize, exposed exactly the temporary token's one allowlisted `tools-list` tool, executed it and returned JARVIS's real result; `wiki-recall` was blocked, and revoked and invalid credentials both returned 401. The temporary token was revoked and the development instance stopped. A smoke call against the older running backend returned 401 before this fix was loaded. The actual Gemma → Hermes tool action remains unverified.
- **Cognee:** real Windows indexing remains blocked by Cognee 1.6.4/LanceDB local version-file handling; keep the feature disabled.
- **God's Eye View:** the source embed and CSP tests pass in isolated validation, but the installed JARVIS application and the running GEV server have not been rebuilt/restarted with the committed changes.

## Selected repositories and optional candidates

These are part of the prior JARVIS directives and must not disappear from the plan. For candidates not in the local clone inventory above, status is **CLONED: no evidence**; the remaining statuses are likewise unclaimed unless a specific result is recorded elsewhere.

| Repository/project | Intended use | Disposition and status |
|---|---|---|
| [simular-ai/Agent-S](https://github.com/simular-ai/Agent-S) (Agent S3) | GUI planning, visual control, verification | Selected computer-use donor/evaluation. Do not infer install or live verification from prior discussion. **CLONED: no evidence; ADAPTED/TESTED/DEPLOYED/LIVE: unverified.** Review local code execution and model/provider requirements before any use. |
| [bytedance/UI-TARS-desktop](https://github.com/bytedance/UI-TARS-desktop) | UI-TARS desktop operator; includes Agent TARS stack | Preserve both names/roles; do not duplicate it as a second runtime. Selected for targeted UI/vision donor review. All delivery states unverified here. |
| [bytedance/UI-TARS](https://github.com/bytedance/UI-TARS) | UI-TARS model/research source | Distinct from UI-TARS Desktop. Keep as model/grounding source; no model tournament now. All delivery states unverified here. |
| [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | Existing Hermes agent/runtime bridge used with JARVIS | Keep as the configured bridge, not a substitute for PersonalJarvis. **CLONED: no evidence in the isolated candidate inventory; ADAPTED: configured/connected previously; TESTED: MCP discovery and individual calls were observed, but end-to-end Gemma tool use is not established; DEPLOYED: present in the user's Hermes setup; LIVE WINDOWS: not verified for the requested Gemma→JARVIS path.** MIT. |
| [microsoft/UFO](https://github.com/microsoft/UFO) (UFO²) | Windows UIA/Win32/WinCOM hybrid automation | Optional Windows donor/evaluation only; MIT. **CLONED: no evidence; other states: no.** Compare against the single selected Windows MCP after core live-path repair. |
| [microsoft/OmniParser](https://github.com/microsoft/OmniParser) | Screenshot-to-UI-element parsing | Optional visual grounding candidate. Keep behind a measured need; audit checkpoint/model licenses and runtime cost. **CLONED: no evidence; other states: no.** |
| [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | Stateful orchestration/graphs | Selected reference/donor; do not replace Dodaios orchestration without a concrete gap and acceptance tests. Statuses unverified. |
| [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents) | Planning, subagents, durable task patterns | Selected reference/donor; do not add a competing supervisor. Statuses unverified. |
| [OpenHands/OpenHands](https://github.com/OpenHands/OpenHands) | Coding-agent sandbox and execution patterns | Selected as a bounded donor/reference, not a second JARVIS app. Statuses unverified. |
| [browser-use/browser-use](https://github.com/browser-use/browser-use) | Browser agent patterns | Optional donor only; overlaps with the configured Playwright path and requires a model endpoint. Statuses unverified. |
| [microsoft/playwright](https://github.com/microsoft/playwright) | Browser automation library | Existing browser stack dependency/reference; do not confuse with the separate Playwright MCP or CLI. |
| [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp) | Browser MCP server | Keep the existing configured browser MCP as the one browser runtime until Hermes tool execution is verified. Apache-2.0; Node-based. |
| [microsoft/playwright-cli](https://github.com/microsoft/playwright-cli) | CLI/skills for browser workflows | Optional authoring/skill workflow, not an additional browser controller. Statuses unverified. |
| [unclecode/crawl4ai](https://github.com/unclecode/crawl4ai) | Local crawling/extraction | Optional only if Playwright/readability extraction fails on a real task; audit attribution and dependency burden. |
| [firecrawl/firecrawl](https://github.com/firecrawl/firecrawl) and its official MCP server | Web search/scrape MCP | Optional, not required for core browsing. Upstream project is AGPL-3.0; self-hosting has service dependencies. No paid API key may be required for the local path. |
| [searxng/searxng](https://github.com/searxng/searxng) | Local metasearch | Optional service only if local search is needed; AGPL-3.0 and adds hosting/maintenance. |
| Apify (platform and community MCPs) | Hosted scraping integrations | Not a single open-source repository; optional only. Do not treat hosted API access as free or required. |
| [supercorp-ai/supergateway](https://github.com/supercorp-ai/supergateway) | stdio-to-HTTP MCP transport bridge | Reuse the existing configured bridge if present; do not add another gateway. MIT. Runtime deployment status must be independently checked. |
| [Wonderwhy-er/DesktopCommanderMCP](https://github.com/wonderwhy-er/DesktopCommanderMCP) | Shell/filesystem/process MCP | Do not add by default: broad machine access duplicates existing scoped tools and increases risk. |
| [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) filesystem server | Filesystem MCP | Reuse existing filesystem capability if configured; do not register duplicate server/tool names. |
| [floatingbrij/desktop-pilot-mcp](https://github.com/floatingbrij/desktop-pilot-mcp) (WinApp MCP) | Target-app Windows UI Automation | Candidate A: scoped UIA controller. Compare with Go MCP and keep only one active desktop controller. |
| [coff33ninja/go-mcp-computer-use](https://github.com/coff33ninja/go-mcp-computer-use) | Windows mouse/keyboard/OCR/UIA/window/system automation | Candidate B: much broader Windows control. Do not run alongside WinApp MCP by default; review elevated-window, process and power controls. |
| [topoteretes/cognee-rs](https://github.com/topoteretes/cognee-rs) | Potential faster Cognee memory worker | Deferred; compare only if benchmarks show the isolated Python Cognee worker is the bottleneck. Do not duplicate or replace it speculatively. |
| [microsoft/graphRAG](https://github.com/microsoft/graphrag) | Multi-document entity/community retrieval | Same repository as the cloned `microsoft/graphrag`; no duplicate clone/runtime. |
| [vasturiano/react-force-graph](https://github.com/vasturiano/react-force-graph) | Existing graph visualization dependency | Reuse in Memory Orb/Wiki Graph; do not create a separate viewer. |
| [CesiumGS/cesium](https://github.com/CesiumGS/cesium) | GEV globe/rendering dependency | Use the existing GEV Cesium build and diagnose its actual shader/WebGL failure. |
| [n8n-io/n8n](https://github.com/n8n-io/n8n) | Optional workflow integration | Not OSI open source; Sustainable Use License. Do not make it the JARVIS orchestrator. |
| [KeygraphHQ/shannon](https://github.com/KeygraphHQ/shannon) | Authorized staging security testing | Optional isolated security tool, not an agent runtime. AGPL-3.0 and actively exploits targets; never aim at production. |
| [open-telemetry/opentelemetry-python](https://github.com/open-telemetry/opentelemetry-python) | Tracing and latency instrumentation | Reuse only if an observability gap remains; Apache-2.0. Do not block chat/voice fixes on a telemetry migration. |

## Voice and model configurations

These are model/runtime configurations, **not repositories**. Keep them in the manifest so they are not lost during repository reconciliation. Do not download, benchmark, switch, or claim live capability without a separate measured task.

| Configuration | Role | State requested by user | Next action |
|---|---|---|---|
| Gemma 12B (llama.cpp endpoint through Hermes) | Main local text/agent model | Retain as target; live tool route remains a priority | Verify endpoint and Gemma → Hermes → authenticated JARVIS MCP end-to-end. |
| Qwen3-VL-8B | Local vision, grounding and tool-capable model | Existing GGUF + F16 projector; optimized local llama.cpp profile and JARVIS provider config | **ADAPTED:** source commits `09d715bef` (8K-safe prompt/history/tool fitting with configurable context hint) and `08504c6a0` (Flash acknowledgment follows local-openai). **TESTED:** focused Windows tests 38/38 for context/provider and 40/40 for acknowledgment; packaged Windows build `2.7.1` starts on scratch port 47824 and returns a genuine `JARVIS_OK` through its WebSocket chat at 904 ms after send. The local `LocalOpenAIFlashAck` adapter also received genuine Qwen responses at 3.78–4.21 s. **DEPLOYED:** built only in isolated staging at `C:\Users\rober\Documents\Codex\.tmp\jarvis-qwen-context-build-20261017-2\dist\Jarvis`; not installed. Qwen used the existing offline Q4_K_XL + F16 projector profile at 127.0.0.1:11440 with 8K context, then was stopped. **VERIFIED IN THE LIVE WINDOWS APPLICATION:** no; the user's installed app was not changed or exercised. The packaged runtime reports voice ready, but audio capture/playback and interruption were not tested. |
| UI-Venus-2-9B | Existing local model configuration | Existing `C:\models\Start-Venus-9B.cmd`; port 11439; user says optimized | Preserved launcher untouched. Port 11439 was confirmed free when checked; Venus was not running during this check. JARVIS has no separate verified Venus provider card/base URL, so do not claim it is actively available to routing. |
| [Holo3.1](https://huggingface.co/Hcompany/Holo-3.1-4B) | Optional computer-use VLM family (H Company), Apache-2.0 model card | Keep selected candidate visible; not a repository/runtime integration | Defer to the bounded computer-use evaluation phase; no download or tournament now. |
| [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Speech recognition | Existing JARVIS voice stack/reference | Voice investigation found a 90-second delegated-turn deadline matching the reported 1:30 timeout; the bounded deadline is now 180 seconds in commit `79d13846e`. **TESTED:** 232 realtime-session tests pass, including timeout cancellation/failure behavior. **DEPLOYED/LIVE WINDOWS:** not established; running app has not been restarted to load this change. |
| [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | Local speech runtime | Existing JARVIS voice stack/reference | Preserve local STT/TTS integration; verify live behavior after watchdog repair. |
| [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl) | Local speech synthesis | Existing voice fallback/reference | GPL-3.0 runtime; check each voice model’s separate license. |
| `Pipecat` | Real-time voice framework reference | Explicitly rejected as core runtime in JARVIS ADR-0037 | Keep architecture ideas only; do not migrate voice loop without a measured blocker. |
| `FlashML-org/FreeToken`, `ggml-org/llama.cpp`, `lich-77/llama.cpp`, `thecodacus/pithagoras` | Earlier Qwen/voice/local-inference references | Canonical llama.cpp is the active upstream; `lich-77` is a fork; the others are not current JARVIS runtimes | No duplication; use a non-canonical fork only for a required, verified patch. |

## Other named projects retained for provenance

- `isair/jarvis`, `PersonalJarvis/PersonalJarvis`, and `zubair-trabzada/holo-gestures` were mentioned in earlier parity prompts. They are separate projects, not the authoritative Robee99 fork. Treat them as selective donors only; no adoption is claimed.
- `SWRM` and `NASA/nasamawk` were explicitly disclaimed in earlier discussion. They are excluded from the implementation roadmap and are not to be cloned or integrated.
- OpenClaw was mentioned as a comparison/interop target, not as the JARVIS runtime. Keep it excluded as a competing orchestrator.
- The initially ambiguous “PyCode” was corrected to PyGPT (`szczyglis-dev/py-gpt`). The two actual PyCode repositories are separately listed above and are not substitutes.
- “Agent TARS” is a project within `bytedance/UI-TARS-desktop`; retain that association rather than creating a duplicate stack entry.
- Apify is a service/platform, not an open-source repo. Firecrawl MCP is tied to Firecrawl and must not be represented as a no-cost hosted API.

## Execution order

1. Restore live chat and repair the voice watchdog/readiness path.
2. Verify Gemma → Hermes → authenticated, approved JARVIS MCP tool execution and result delivery.
3. Complete the isolated Cognee deployment and authorized-source lifecycle acceptance; then run the real local GraphRAG multi-document acceptance with the configured llama.cpp endpoint.
4. Reproduce/fix the GEV shader failure, verify rendering and supported layers, then connect it to Command Center without discarding pre-existing staged changes.
5. Port selected compatible Command Center/Mission Control UI components from Dix01 and mmAbdelhay when they improve the existing product; retain separate CLONED/ADAPTED/TESTED/DEPLOYED/LIVE-WINDOWS evidence.
6. Only after the active paths pass, do the bounded UFO²/OmniParser or single-controller comparisons. Defer Cognee Rust until benchmark evidence. Defer broad model tournaments and framework migrations.

## Evidence and update rules

- Record exact source URL, full commit SHA, license, and dependency/runtime requirements whenever a clone is added or refreshed.
- Record test command, test scope, result, Windows runtime/build identity, and relevant logs for **TESTED**.
- Record the specific build/configuration for **DEPLOYED**.
- Record actual user-app path/version and the tool/model/action/result for **VERIFIED IN THE LIVE WINDOWS APPLICATION**.
- Preserve uncommitted files, Git history, database/user data, and God's Eye View's pre-existing staged and unstaged changes. Never reset or overwrite them to update this manifest.
