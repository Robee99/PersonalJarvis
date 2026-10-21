# Canonical MCP routing and live Qwen verification, 2026-10-21

Branch: `codex/voice-text-repair-20261020`; starting HEAD
`8a6bdf18c38e65ae74d1a6527c510f0246d83aad`.

## Installed application and provider selection

The installed Personal Jarvis 3.1.1 backend responds on port 47821. Both
installed executable hashes match the previously prepared MCP repair:

- GUI: `b5cbed810ca219caf5f63e9ccf34db33faae584b7f681098b40f0c7b6d03578f`
- CLI: `ddf95ae848bdc7db76aba7109896ed50752ccbb840a407a94c5c78abeac0ed8f`

Those artifacts replace two brain modules while preserving 13,787 other
compressed modules, the existing HUD, dependencies and user data. This
supersedes the previous report's waiting-for-exit activation status.

The live brain had retained the stale `local-gemma::gemma-4-12b-qat` selection.
The supported `/api/local-servers/use` endpoint selected the existing local
Qwen endpoint on port 11440. The active brain now reports
`hermes / local-qwen-vl::qwen3-vl-8b`. Hermes remains the orchestrator; its
existing provider was reused without credential edits or a restart.
The existing 65,536-token context setting was preserved. No model download,
new inference process, paid request or competing controller was introduced.

## Actual model result and acceptance limit

Diagnostic session: `30c1c9b6c0424c2ea5287ebeb1a2ce02`.
Completed turn: `61fd26bd84f2475fbc26cd03bd4d09fe`.

The turn-start event confirms Hermes with the explicit Qwen selection. Its
upstream log records a real `mcp__windows__DisplayInventory` call at
15:05:23 local time, completing in 0.02 seconds. The model's final response
correctly reports the primary display as 1920x1080 with 125% scaling. Total
turn duration was 117.483 seconds, including upstream history compaction and
seven model API calls. Cumulative input across those calls was 243,981 tokens;
that is not a single context window size.

**The constrained end-to-end acceptance did not pass.** Hermes used its direct
Windows MCP, rather than the authenticated JARVIS outward gateway, and tried
unrequested memory maintenance and a terminal invocation first. The terminal
attempt to invoke the MCP tool as a shell command failed with exit 127.
The diagnostic requested neither operation. No successful desktop mutation
was observed. A final correct answer does not establish correct routing or
permission enforcement. Native chat events also do not mirror these upstream
tool receipts; the genuine receipts are in the Hermes log.

The earlier attempt was cancelled after its actual start event disagreed
with the submitted provider metadata. It is explicitly marked invalid in
its evidence file. No user conversation was deleted or rewritten.

## Canonical route fix

With a later SPA catch-all, Starlette's ordinary mount misses the slashless
`/api/control/mcp` root: GET serves the SPA and POST/DELETE return 405 before
MCP authentication. Without that catch-all it may issue a 307 redirect that
some MCP clients do not follow for POST.

`MCPMount` retries Starlette's own matcher with a trailing slash only after
an HTTP no-match. Production uses that mount. Existing transport, root-path
handling, scoped-token verification and executor permissions are unchanged.
Neighboring SPA paths remain outside the mount.

Changed source: `jarvis/ui/web/mcp_server_routes.py`,
`jarvis/ui/web/server.py`, and `tests/unit/mcp/test_agent_mcp_routes.py`.

Verification on Windows:

- Before implementation: canonical/neighbor regression subset, 6 failed and
  7 passed, reproducing SPA interception.
- Focused route, chat, token, origin and export suite: **71 passed**.
- Expanded MCP/auth/hangup/watchdog/language suite: **321 passed, 18 failed**.
  All 18 failures reproduce with the exact starting HEAD source in an
  isolated import overlay: 14 spoken-consent fixture failures and four
  language-test dispatcher mocks rejecting `history_override`. They were
  not suppressed or reclassified as passing.
- Ruff for the route implementation and regression file: passed.
- Scoped whitespace check: passed. Independent review: no issues found.

The new canonical mount is source-tested, not yet installed. The running
app still has the earlier repaired brain bundle. This change does not claim
the complete Qwen-to-Hermes-to-authenticated-JARVIS acceptance is finished.

## Resources and rollback

At the final snapshot, the production app working set was 767.5 MiB. The
RTX 4060 Laptop GPU reported 7,714 of 8,188 MiB in use and 0% utilization.
These are instantaneous measurements, not a performance benchmark. No
additional GPU model was loaded.

Runtime evidence is under the chat workspace's
`outputs/mcp-surface-repair-20261020/`, including
`live-hermes-qwen-mcp-20261021.json`, `qwen-selection-20261021.json`, and
`canonical-port-baseline/head-failures.txt`.

Provider rollback: use the existing provider selector or configuration writer
to restore the previous selection from the saved pre-Qwen configuration;
never raw-write a live TOML file. Backup:
`%LOCALAPPDATA%/Jarvis/backups/mcp-surface-20261020/pre-qwen-selection-20261021.toml`.

Source rollback: revert only this canonical-route commit. It does not change
installed files. The earlier installed brain repair can be rolled back after
normal app closure using the executable backups documented in
`mcp-surface-repair-20261020.md`. Preserve all databases and MCP credentials.
