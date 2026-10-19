# Selected conversation memory lifecycle port: 2026-10-20

## Scope and branch

Branch: `codex/voice-text-repair-20261020`, based on the current HUD 3.1.1
branch. The original integration checkout and its unrelated changes were
not reset, staged, or overwritten.

The HUD branch already contains a real conversation acceptance test and a
`/api/wiki/semantic-index` call to `list_conversation_sources`, but its
Cognee adapter lacks that implementation. This change ports the previously
reviewed conversation lifecycle from `50a7f30e05a64235f5fcc0f190182b173206e4bd`
while preserving the HUD branch's Windows temporary-directory, model-cache,
and asynchronous wiki file I/O fixes.

## Changed files

- `jarvis/memory/cognee_adapter.py`: selected conversation rendering,
  deterministic source IDs, thread provenance, idempotent indexing, explicit
  forgetting, typed source listings and conversation-only retrieval.
  Disabled Cognee rejects indexing before consulting a cached record.
- `jarvis/memory/cognee_worker.py`: backward-compatible typed registry
  migration, conversation metadata, wiki/conversation retrieval isolation,
  immutable document version IDs and durable deletion/update tombstones.
- `tests/unit/test_cognee_legacy_registry.py`: complete legacy schema
  handling, damaged/partial schema rejection, private-source filtering,
  and the disabled cached-index regression.
- `tests/integration/test_cognee_conversation_runtime.py`: additional actual
  idempotency, persisted provenance, cross-role isolation, update and
  deletion assertions. Existing assertions were preserved.

## Verification

- Before the port, the focused new unit tests and existing conversation
  acceptance failed: 8 failures and 3 passes, including missing adapter
  methods. The disabled-cache regression separately failed before its fix.
- Focused adapter/source tests: **15 passed**.
- Existing wiki HTTP route tests, including `/api/wiki/semantic-index`:
  **38 passed** using real isolated files and SQLite source registries.
- First actual Windows conversation lifecycle run: **1 passed in 163.19 s**.
  This used Cognee and its cached local CPU models, not a fake adapter.
- Final actual wiki and conversation lifecycle run: **2 passed in 304.43 s**.
- Required routing, output, hangup and language contracts: **598 passed,
  1 failed**. The unchanged failure is
  `test_router_tools_is_pure_dispatcher_set`, which omits existing
  `cognee-source` and `kept-archive-search` names. The identical failure on
  exact base `d91d527afa` was already reproduced and recorded in
  `docs/diagnostics/voice-text-repair-20261020.md`. No expected tool set or
  baseline was relaxed.
- Scoped Ruff and `git diff --check` pass. Independent read-only review
  found no important or critical issue, including the feature-flag fix.

Both real lifecycle tests create synthetic records in fresh, separate
Windows scratch stores. They exercise index, new-process recall, update,
old-content exclusion, delete and new-process deletion exclusion. The
conversation test additionally proves that conversation recall does not
read or reconcile a changed wiki file containing synthetic sensitive text.

The isolated Python runtime is
`C:/models/jarvis-cognee-runtime-final/Scripts/python.exe`. It runs without
hosted provider credentials, with Hugging Face/Transformers offline mode,
existing cached models and two configured CPU math threads. One actual
worker sample used 753 MiB of resident memory; this is not a measurement
of total process-tree peak memory. Final results are retained outside the
checkout at `%LOCALAPPDATA%/Temp/jmem-1020-final-results.xml`.

## Deployment boundary and rollback

This milestone is source implementation plus genuine isolated Windows
runtime acceptance. It does not activate Cognee for real user records,
change feature flags, replace the installed application's memory modules,
or prove a microphone conversation. Native wiki/FTS remains the existing
fallback. No production indexing, model download or paid provider was used.

The previously repaired installed app remains healthy: `/api/health`
returns 3.1.1/default; voice reports ready/listening; the brain reports
local-openai/qwen3-vl-8b. That health check is separate from memory deployment.
God's Eye View and its staged changes were untouched.

Rollback of this isolated milestone is a revert of its scoped commit.
Original source snapshots are also retained under the existing repair
artifact's `memory-port-baseline` directory. Do not reset the original
checkout or delete user data to undo this change.
