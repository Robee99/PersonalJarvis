# PersonalJarvis integration reconciliation — 2026-10-24

## Repository and scope

The uploaded personal_jarvis_integration_suite.py contains proposed standalone implementations rather than a patch against this production tree. This work reconciles the reported defects with existing components and does not register the upload's parallel executor, memory runtime, or invoice engine.

| Item | Exact value |
|---|---|
| Integration branch | integration/jarvis-suite-repair-20261024 |
| Base commit | 2b805e856a17aa971c743cd3736b8bf92aa80daf |
| Base branch | codex-gemma-mcp-fix-20261014 |
| Worktree | C:\Users\rober\Documents\Codex\2026-10-24\referenced-chatgpt-conversation-this-is-an\work\PersonalJarvis-integration |
| Original checkouts | C:\Users\rober\PersonalJarvis and C:\Users\rober\PersonalJarvis-fork |
| Existing author identity | Robee99 <157852291+Robee99@users.noreply.github.com> |
| Identity evidence | Existing commit 31e1dbecc56a906823db0cb5483d2a3d4f8d6088 |

Both original checkouts were dirty before this work. Application changes are confined to the integration worktree; scratch data and exported reports are inside this task's directory. User databases, configurations, installed runtimes and editable-install pins were not changed. No merge, push, installation or deployment was performed. Git author identity and versioned hooks are selected per commit invocation without changing shared Git configuration.

The base was selected after comparing local branches and linked worktrees, including main, jarvis/local-qwen-build, codex/jarvis-takeover, codex/voice-text-repair-20261020, and codex-gemma-mcp-fix-20261014. No fetch or checkout of a dirty source tree was needed. The exact local base above is authoritative; it is not claimed to be the current remote tip.

## Eight findings

| Finding | Production evidence and action | Status |
|---|---|---|
| Approval replay | Production tickets are armed before publication, ignore decisions without a matching pending ticket and preserve the first answer. ToolExecutor.execute_confirmed() pops its pending action before execution. Added a replay regression while another call is pending; existing executor tests prove a second confirmation cannot repeat the side effect. | Existing protection verified. |
| False verified=True fallback | The uploaded fallback is absent from production. Evidence-enforcement tests and real CU engine state-machine tests cover accepted observed proof and rejected completion claims. | Existing evidence policy verified. |
| Invalid invoice PDF | A synthetic probe extracted only the upload's four invoice classes. A real pypdf parser rejects its output with PdfReadError: startxref not found. Production document code extracts documents but has no invoice renderer. | Reproduced in upload; invoice generation remains unsupported and unintegrated. |
| Duplicate invoice numbering | Two upload engine instances constructed against the same synthetic ledger allocate the same number and overwrite the same artifact. Two rows remain for that number. An in-memory sequence and JSON append do not provide atomic allocation. | Reproduced in upload; no production ledger exists to repair. |
| SSRF: private networks, IPv6, redirects, DNS | Existing private-address and mixed-answer checks are retained. Validated IPs are now pinned inside the existing HTTP guard transport, preventing a second hostname lookup during connection. Connection failure may fall back only within the same validated answer snapshot. Original URLs, cookie scopes, redirect origins, Host and TLS SNI remain intact. Configured certificate authorities are preserved; hostname/certificate verification stays enabled. Environment proxies and default keepalive reuse are disabled on this occasional import path to prevent independent proxy resolution and TLS connection reuse across names sharing an IP. | Fixed; unit, real HTTPX/httpcore integration and production import-endpoint tests pass. |
| Cross-conversation memory collisions | Base includes ancestor 50a7f30e05, fix(memory): isolate conversation retrieval and verify Cognee lifecycle. New tests prove punctuation-distinct thread IDs retain different source IDs/paths, updates retain stable identity, forgetting one selects its source, and wiki/conversation searches use different source scopes. | Production adapter boundary verified with a fake worker. Real Cognee runtime acceptance is unrun. |
| MCP prefix overmatching | Reproduced /api/control/mcpagents being stripped as if beneath /api/control/mcp. Root stripping now requires an exact mount or a slash segment boundary. | Fixed; regression failed before the fix and passed afterward. |
| Brittle read-only intent | Production uses trusted chat policy and tool risk information rather than the uploaded prompt regex. The plan-mode contract blocks a safe-tier sender even when whitelisted. | Existing production contract verified. |

Invoice generation remains an explicit capability gap. Its unsafe standalone implementation has not been exposed to production. A production invoice feature would need a real renderer and transactional, durable numbering, separately scoped within existing tool and artifact interfaces.

## Async test stall

A six-second faulthandler probe showed the sandbox blocked Windows in socket._fallback_socketpair() while creating the event loop. The identical trivial asyncio.run(asyncio.sleep(0)) completed immediately outside the sandbox.

Affected tests subsequently ran outside the sandbox with an offline runner that rejects socket connections except Windows' internal socketpair setup. Real Cognee runtime opt-ins were unset; storage and fixtures used synthetic temporary data. No live provider or model calls were made.

The existing test environment is C:\Users\rober\Documents\Codex\2026-10-14\jarvis-build-venv\Scripts\python.exe: Python 3.13.15, HTTPX 0.28.1, httpcore 1.0.9. No packages were installed. Imports were confirmed to resolve from this worktree. The preflight import checks passed; its attempt to set shared hook configuration was denied by the sandbox, so the commit instead selects versioned hooks through a per-command Git option.

## Verification

| Check | Result |
|---|---|
| DNS, approval, memory adapter, updater, MCP, skill import, gateway, evidence, branding and CU completion suites | 332 passed, 1 existing failure, 8 skipped in 11.80 seconds |
| Read-only contract plus required routing, output-filter, hangup-reason and turn-language guards | 599 passed, 1 existing failure in 26.12 seconds |
| Total across these disjoint runs | 931 passed, 2 existing failures, 8 skipped |
| Ruff on all seven changed Python files | Passed |
| Working/staged whitespace checks | Passed |
| Synthetic invoice probe | Invalid PDF and duplicate number/artifact overwrite reproduced |
| Independent final code review | No outstanding findings after configured-CA compatibility correction |

Both failing tests were rerun against an export of exact base commit 2b805e856a17aa971c743cd3736b8bf92aa80daf under the same interpreter and offline runner. Failure identities and causes match:

1. tests/unit/ui/web/test_skills_routes.py::test_creator_author_writes_a_draft_skill_with_a_brain expects display name Mail Morgen; existing production returns normalized slug mail-morgen.
2. tests/unit/brain/test_routing.py::test_router_tools_is_pure_dispatcher_set omits existing cognee-source and kept-archive-search tools from its exact-set expectation.

These unrelated expectations were neither weakened nor excluded. They remain base-branch backlog items. The full repository is not claimed green.

Eight skips comprise six POSIX/macOS updater cases unavailable on Windows and two opt-in real Cognee runtime cases. The latter require isolated worker and embedding/runtime operations; these runs verify the production adapter with a fake worker. Physical desktop operations, other operating systems, real Cognee embeddings and unselected repository suites remain unverified.

The expanded regressions also caught flaws in the initially staged DNS patch: changing the client request URL broke redirect/cookie origin semantics, and a bytes-valued SNI did not match the backend's string interface. Both were reproduced and corrected. Further tests prove IPv4 fallback when validated IPv6 is unreachable, preserve configured CA roots, exclude environment proxies, and prevent retries after read/write failure or an HTTP 503 response.

## Current updater endpoints

The earlier explicitly authorized metadata-only HEAD checks on 2026-10-24 returned HTTP 404 with no Location for each unauthenticated endpoint:

- https://api.github.com/repos/Robee99/PersonalJarvis/releases/latest
- https://github.com/Robee99/PersonalJarvis/releases/latest/download/PersonalJarvis-Setup-x64.exe
- https://github.com/Robee99/PersonalJarvis/releases/latest/download/installers-SHA256SUMS.txt

No live redirect chain was observable and no asset was downloaded. A 404 does not distinguish a private/unavailable repository from an absent latest release or asset. Offline tests of the existing updater's real HTTP client verify HTTPS asset redirects, HTTP downgrade refusal, checksums, streaming limits and progress. Installer handover runs through fakes only.

## Changed paths

- jarvis/core/http_guard.py
- jarvis/ui/web/mcp_server_routes.py
- tests/unit/core/test_http_guard.py
- tests/unit/mcp/test_agent_mcp_routes.py
- tests/unit/safety/test_approval_workflow.py
- tests/unit/test_cognee_conversation_boundary.py
- tests/unit/web/test_skills_import_route.py
- docs/reports/personaljarvis-integration-suite-reconciliation-2026-10-24.md

The integration branch tip identifies the commit containing this report. The exported task status report records the final commit ID, patch and test evidence. Merge and deployment require the user's explicit authorization.
