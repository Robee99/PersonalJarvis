# Qwen → Hermes → authenticated JARVIS MCP — 2026-10-19

Branch: `codex-gemma-mcp-fix-20261014`.
Parent revision: `18ac3c252653e0d3cefe41daa0b7add9eb44354a`.

## Confirmed defect and source correction

Real Hermes Streamable HTTP discovery against the actual source backend failed
with HTTP 405 at the configured canonical URL `/api/control/mcp`. Its fallback
SSE attempt also failed. The ordinary Starlette Mount requires a slash after
the mount root; the application's final SPA route matches first and prevents
the router's automatic slash redirect. GET therefore served the SPA and POST
returned 405. Existing tests mounted only MCP and followed redirects, hiding
the failure seen in the actual application.

`MCPMount` now uses normal Starlette matching and, only after an HTTP non-match,
tries the slash form for matching. It retains the original request path and
Starlette's generated child scope/root path. Existing ASGI authorization,
per-surface token restrictions, approval gateway and tool dispatch are intact.
The real server and transport tests use the same mount class. Tests include
the SPA fallback, nested application mounts, GET/POST/DELETE and neighboring
SPA URLs. No config URL migration is needed.

Files changed:

- `jarvis/ui/web/mcp_server_routes.py`
- `jarvis/ui/web/server.py`
- `tests/unit/mcp/test_agent_mcp_routes.py`
- This evidence note.

## Real local execution proof

The test used the installed Hermes agent source and its existing `venv`, the
JARVIS source backend on isolated localhost port 47922, and the actual local
Qwen3-VL-8B endpoint on 11440. Production port 47821 and packaged app files
were not replaced. A temporary individually revocable `tools` token allowed
only the safe `describe-app-settings` tool. Execution went through JARVIS's
normal supervisor gateway; no direct tool implementation call was substituted.

Observed sequence:

1. Qwen's existing model proxy loaded its configured profile in **19.70 s**.
   The real `/props` response declared vision/video input, no audio input,
   tool calling and **65,536 context tokens**, with one slot.
2. Hermes initialized and discovered the canonical slashless MCP endpoint:
   `mcp__jarvisacceptance__describe_app_settings`.
3. Qwen generated that exact function call with `{}` arguments.
4. Hermes dispatched it to authenticated JARVIS MCP; the tool returned the
   genuine settings snapshot. No settings were changed.
5. Qwen read the result and answered:
   `{"primary_provider":"local-openai","mcp_server_count":0}`.
6. The answer was compared programmatically with the active provider row and
   MCP list length in the actual returned tool payload. Both matched.

The verified agent turn used **two local model API calls in 4.34 seconds**.
An earlier discovery-wrapper-only turn searched/described but never executed
the MCP tool and invented an answer; it was rejected as evidence. The final
single-tool test used Hermes's supported direct-tool setting and explicit
65,536-token context in a temporary home. Production tool-search configuration
was not changed. This narrow proof does not establish reliable selection from
the user's entire configured tool catalog.

The first harness postprocessor expected bare JSON and the wrong provider
shape. Hermes wraps external results as untrusted data and the provider field
is a list with an active row. Verification was corrected against the saved
actual message; no fabricated payload or replacement model response was used.

## Security and automated checks

Real HTTP checks on the corrected source backend:

- Valid scoped token: initialize HTTP **200**.
- Invalid token: HTTP **401** with a valid local Origin, proving the inner
  MCP gate still rejects credentials. Without Origin the outer CSRF gate
  rejected malformed credentials with **403**.
- Tools token on agent-ecosystem surface: HTTP **401**.
- `switch-provider` outside the token's allowlist: MCP error result, no action.
- Independently reloaded token store verified persistence.
- Revocation written through another TokenStore: subsequent initialize **401**.
- Both temporary test tokens were revoked; the diagnostic backend shut down
  normally with exit code zero.

Red regression: **6 failed, 7 passed**, reproducing SPA/405 behavior before the
fix. Final routing/token/CSRF/agent-contract/boot-import suite: **69 passed**,
0 failed, in **2.53 s**. Ruff and scoped diff checks passed. Independent read-only
review found no important issues in the routing fix.

The suite emitted two existing Starlette/httpx/anyio deprecation warnings. The
repository's test shutdown hook also reported three remaining aiosqlite worker
threads in the combined suite and performed its existing hard-exit cleanup;
this is not evidence of a clean full-suite teardown. The separate real backend
shutdown completed normally.

The full desktop/voice boot-budget guard was not run: it detects an audio input
and would start a voice stack while the loaded Qwen profile occupies 7,711 of
8,188 MiB VRAM. The dependency-light boot import regression passed, and the real
headless backend served successfully. Voice startup, latency and microphone
acceptance remain unverified.

## Artifacts and remaining limits

Local evidence directory:
`%TEMP%\jqe-1019\`

- `hermes-before-mount-fix.log`: actual failed discovery before correction.
- `backend-fixed.log`: corrected source process, tool traffic and shutdown.
- `hermes-result.json`: actual model and protected tool messages.
- `verified-answer.json`: sanitized observed/result comparison and timing.
- `security-results.json`: real authorization/revocation outcomes.
- `final-tests.xml`: final 69-test result.

`credential.json` and the temporary Hermes config contain revoked test
credentials; do not copy them into commits or production configuration.
Hermes also reported an existing source-update completion failure because its
Python 3.14.7 executable was denied access; the successful proof used the
existing Python 3.11 virtual environment. That update issue was not repaired.

Status: TESTED through the real local Qwen/Hermes/source-JARVIS stack on Windows.
Not DEPLOYED or VERIFIED IN THE PACKAGED WINDOWS APPLICATION. General desktop
clicking, voice, all MCP servers, GEV rendering, and installed-build lineage are
separate unfinished acceptance/deployment tasks. No paid-provider fallback,
new repository clone, model-profile edit or GUI automation was used.

## Rollback

Review/revert this four-file commit from a clean tree; do not reset the shared
working tree or discard its unrelated changes. No production settings need
restoring. The temporary source process is stopped and its tokens revoked.
The user's existing Qwen profile remains loaded with its original 65k settings.
