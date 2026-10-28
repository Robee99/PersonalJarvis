# Cognee Windows conversation lifecycle: 2026-10-18

## Scope and provenance

Branch: `codex-gemma-mcp-fix-20261014`.
Parent HEAD: `3d5bfa3770ece148ae31507ff0f2e034165fc6ef`.

This milestone finalizes the previously pending conversation adapter/worker
implementation and fixes regressions found during real Windows acceptance and
code review. It does not activate Cognee in user configuration, replace the
installed application, change model services, or alter the separate God's Eye
View checkout.

Changed files:

- `jarvis/memory/cognee_adapter.py`
- `jarvis/memory/cognee_worker.py`
- `tests/integration/test_cognee_conversation_runtime.py`
- `tests/unit/test_cognee_legacy_registry.py`
- This evidence note.

## Behavior exercised

The real isolated worker indexed a synthetic selected conversation, restarted
for subsequent requests, and returned the actual source ID and thread reference.
Repeated indexing after recreating the adapter returned unchanged, without a
second source record. Updating the conversation preserved its stable source ID
and excluded the old text from retrieval. Forgetting it removed its active source
listing and excluded it after another adapter/worker restart. Tool-role payloads
were excluded from the indexed transcript.

Three safety corrections are included:

1. Pre-conversation wiki registries retain source visibility and secret filtering
   before worker schema migration. Missing required columns raise an explicit
   error instead of silently bypassing reconciliation.
2. Conversation searches no longer reconcile unrelated wiki files. A real
   regression first demonstrated that a wiki note edited to contain a synthetic
   credential was ingested by conversation search. The corrected test verifies
   that conversation retrieval leaves the wiki version hash unchanged.
3. Conversation source listing recognizes only a complete legacy wiki schema;
   partial conversation schemas and missing required fields fail explicitly.

## Genuine verification

Host: Windows, main checkout virtual environment running pytest; separate worker
`C:\models\jarvis-cognee-runtime-final\Scripts\python.exe`.
Cognee 1.6.4; Python 3.11.15; PyTorch 2.14.1+cpu, CUDA unavailable in this worker.
The existing cached GLiNER small and FastEmbed models were reused. Offline
Hugging Face/Transformers settings were enabled. Hosted API keys are stripped at
the worker boundary. No model downloads or paid inference were performed.

- Focused unit tests: **14 passed**, 0 failed (1.15 seconds).
- Final real conversation lifecycle plus cross-role regression: **1 passed**,
  0 skipped, 0 failed (105.81 seconds).
- Earlier real lifecycle runs: 103.91, 84.87 and 89.77 seconds, all passed.
- Regression before role-isolation fix: 1 failed at the wiki hash assertion
  after 54.74 seconds, confirming the unintended ingestion.
- Expanded registry regressions before schema fix: 5 failed, 5 passed.
- Ruff and scoped `git diff --check`: passed.
- Independent read-only code review: both reported issues addressed; no further
  important findings in the final delta.

The test suite emitted two existing Starlette/httpx/anyio deprecation warnings.
One retry reused a fixture left active by the deliberately failing regression
and correctly returned unchanged; its fresh-ingestion assertion failed. The
final acceptance used a fresh isolated registry with copied model caches.

Final runtime artifacts:

`%TEMP%\jarvis-cognee-cross-role-verified\`

- `pytest.log` and `results.xml`: final real acceptance.
- `data/source-state.sqlite3`: actual persisted synthetic registry.
- `data/logs/`: real Cognee worker logs.

Unit XML and failing regression evidence:

`%TEMP%\jarvis-cognee-heartbeat-20261018-idempotency\`

- `final-unit-results.xml`
- `cross-role-red.xml` and `cross-role-red.log`

## Resources and limits

Worker environment limits OpenMP/MKL threads to two and uses GLiNER small batch
size two with 256-word windows. A process sample during the final run showed
about 1.50 GiB resident memory and 1.65 GiB process-reported peak for the inference
worker. This is a single process sample, not a bound on aggregate application
memory or a complete benchmark. Each request starts a fresh worker, so cold
initialization still dominates latency; a persistent serialized worker needs a
separate recovery/locking design before changing that isolation model.

## Deployment status and rollback

Status: TESTED IN AN ISOLATED WINDOWS WORKER. Not DEPLOYED or VERIFIED IN THE
LIVE WINDOWS APPLICATION. Cognee remains opt-in. The broader live provider,
voice, correct installed-build lineage and GEV integration issues are separate.
The old blanket Windows indexing failure was not reproduced in the authorized
local worker environment; normal application launch and production activation
have not been exercised by this milestone.

No user conversations, notes, production indexes or configuration were changed.
Native-memory behavior remains the fallback. Rollback for production requires
no configuration change because this feature remains disabled. To undo this
source milestone, review its five-file commit and revert it from a clean tree;
do not reset or discard the other existing working-tree changes. Preserve the
synthetic registry if retaining reproducibility evidence; it is not user data.
