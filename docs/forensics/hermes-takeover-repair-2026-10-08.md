# Hermes takeover: first repair batch

Base: `8ba3c3c010dc6d48fee93aa8e48c1d65c8397ed6` on `jarvis/local-qwen-build`.

## Changes and evidence

- Stop now requests native cancellation for tracked active and approval-waiting conversation runs, across voice and typed brain instances. It checks the HTTP response and native status; a rejected stop remains available for retry. The response says the request was accepted, without claiming the native process has already finished. Late queued events cannot re-arm a cancelled approval.
- The voice pipeline's existing pending-confirmation check now sees native approvals, so a single-turn call stays open for the answer. Continue starts a fresh run using the same native conversation history; old approvals are never granted again. This is conversation continuation, not checkpoint restoration.
- Native tool starts, successful completions, failures, and interrupted calls reach the existing chat timeline and persisted event log. Events are turn-scoped and previews pass through the existing secret redactor. A started or failed tool no longer counts as successful action evidence. The native API currently has no per-call ID, so starts and completions of the same tool are paired in stream order within each run.
- Explicit look-only requests are refused before starting an agent run. The installed native API has no public per-run read-only policy. Its old post-start denylist could not prevent terminal, code, or MCP writes. A policy refusal is not retried as a network/provider failure. Existing Plan-mode refusal remains.
- Memory import canonicalizes Markdown newlines, so unchanged Windows files and retries after cancellation do not rewrite/re-index every page. The filename test now exercises portable filename characters and separately checks drive-prefix containment.

## Validation

- New failing regression tests reproduced the Stop, voice-confirmation, missing-tool-card, and premature-success defects before their fixes.
- Expanded Python suite: 733 passed, 1 skipped, including the four required routing, output-filter, hangup-reason, and language guards.
- After the final queued-event and preview-isolation checks: 36 passed in the affected session/chat suites, including the two added regressions.
- Frontend: 58 passed across the chat reducer, WorkTrace, Hermes armory, and memory import views.
- Lint compared against the exact base: 33 existing findings in the touched files, zero new findings. No baseline was changed. Whitespace check passes.
- No real model calls, desktop actions, paid-provider probes, or changes to the installed app were used for these tests.

## Remaining work

This batch is not a full parity or installer acceptance certificate.

- Native approvals still use the existing yes/no turn flow; clickable Hermes approval cards and richer subagent activity need integration.
- Stop of already-detached native delegations needs its own native task-cancellation contract; stopping the delivery watcher alone is not proof that detached work stopped.
- Enforced read-only native execution requires a gateway permission API. Look-only is deliberately unavailable through this adapter until that can be enforced before tool execution.
- The wider connected-app bridge, one canonical model selection across surfaces, project intelligence with provenance, Orb v2, and navigation work remain separate implementation stages.
- Live microphone/STT/TTS, installer installation, browser/desktop outcome verification, and the full acceptance matrix remain unverified for this batch.

## Detached native cancellation follow-up

The packaged `jarvis-control` Hermes plugin registers authenticated HTTP handlers
through the existing native platform factory. It does not modify Hermes core files.
Its roster exposes identifiers and status only. Stop invokes the real native child
interrupt callback in the captured profile context. Native session compression
lineage, API source, conversation key and profile home constrain ownership. Failed
callbacks remain retryable; accepted interrupts are not sent twice. Acknowledgement
means an interrupt request was accepted, not that every child has finished unwinding.

Jarvis queries this native roster on Stop, including when a foreground run already
completed or Jarvis restarted. Delivery watchers retire only after native requests
succeed. Missing controls produce an actionable safe error. The free-voice command
ships/enables the plugin and preserves any unmanaged plugin with the same name.
Loading/updating it requires a Hermes gateway restart.

Validation on 2026-10-08:

- 755 affected/shared-guard Python tests passed; 1 skipped.
- 2 provisioning tests passed (repeat setup and unmanaged-plugin preservation).
- Real native contract passed via Hermes `scripts/run_tests.sh`, against native
  source `6ec38bcc4e5b320711844c8cdb683ed2cbf623b8`: real plugin discovery, real API
  adapter/session DB, detached executor worker, rejected callback/retry, duplicate
  Stop and A -> B -> A profile isolation with matching session IDs.
- The native fixture child returned and its real registry state became `interrupted`.
  No model, real account credentials or desktop action was used.

The installed app/gateway have not been changed by these checks. Clickable native
approval cards, policy response rendering, canonical selection, connector bridge,
project intelligence and installer/device acceptance remain separate follow-ups.

## Intentional read-only refusal follow-up

Unsupported look-only and Plan requests now return a concise, localized response
through the normal voice/chat response path. The policy exception still bypasses
provider retry, fallback and outage classification. No native run starts. A pending
native approval is stopped before the refusal. Chat's persisted `turn_finished`
receipt carries `policy_refusal=read_only_unavailable`; the displayed response
explicitly says no new run or action occurred. Refusals do not launch memory curation.

659 affected Python and shared-guard tests passed, including saved chat evidence
and English/German/Spanish voice response language. These remain fixture checks,
not microphone or installed-app acceptance. UTF-8 text was preserved after correcting
an editing-helper encoding defect; no installed file was changed.


## Native approval cards (follow-up stage)

Native `approval.request` events now create ordinary persisted chat permission cards. The adapter owns one pending exact request identity per Hermes conversation. Cards are evidence routes only: they contain no second permission Future or standing grant. Clicks, spoken answers and Stop resolve the same native request. The UI offers **Allow once** and **Deny**, never an always/session grant. Original turn trace and chat identity follow resumed native work; starts alone never count as success.

Native denial, cancellation and expiry retire the card. Reopened persisted cards without live authority expire. A failed native resolver leaves the exact pending identity retryable and returns a safe explanation. Native `approval_not_pending` retires the card and requests native Stop. Command/description/result previews are redacted. Approval-only outcomes show permission status, rather than a completed action.

Validation on the supported private MCP environment (`mcp==1.28.1`):

- Shared voice/chat/adapter suites and all four routing/language guards: **1082 passed, 1 skipped, 2 baseline failures**. Exact committed HEAD `707158c` comparison under the same environment: **1073 passed, 1 skipped, the same 2 failures**. Both are `test_runner_brain.py` tool-set expectations with an extra `society_browser` after earlier suites; they pass in isolation. No expectations or baseline were weakened.
- Isolated affected approval, immediate cancellation, MCP harness and brain runner contracts: **41 passed**, including 11 new approval cases. Cases cover voice/chat origin with allow/deny, Stop, stale/replayed clicks, other-chat rejection, redaction, restart expiry, native expiry, unavailable-resolver retry and repeated approvals retaining their original turn after the visible chat changes.
- Frontend reducers, actual approval component, Hermes Armory and Memory Orb import: **63 passed**. TypeScript and production build passed; actual component inspected in light/dark themes with harmless fixture cards.
- Ruff identities compared with HEAD: **32 existing findings, 0 added**. Windows generated HTML line endings normalized; diff whitespace checked.

The production UI bundle is included. These are source/contract/component checks, not microphone, installed-app, live-provider, connector or installer acceptance. Installed Jarvis and the installed Hermes core were not changed by this stage. P1 canonical model ownership, wider connected-app bridging and P2 project intelligence remain open.


## Canonical Hermes model selection (P1 follow-up)

The native Hermes conversation's durable `browser_model_lock` is the selected
model authority. The voice settings picker and typed chat picker both read/write
this preference through the packaged control plugin. Main-conversation turns send
the virtual `hermes-agent` model so a stale Jarvis per-chat or voice value cannot
supersede the native choice. Other isolated Hermes sessions keep their explicit
selection contract. Existing Jarvis session rows are display/history projections;
acknowledged changes refresh their live UI, rather than re-pin a second config.

Picking before the first turn creates the native API conversation and its peer in
one native atomic upsert, preserving an existing peer in a race. Reads/writes check
the authenticated profile, conversation key and resumed lineage. Selection writes
require a durable acknowledgement. Failures return a safe retry/setup explanation,
leave the old preference intact and never run a health/model probe. Controls have
an eight-second timeout. Updating the packaged plugin requires free-voice setup
and a Hermes gateway restart; this stage has not updated the installed gateway.

An explicit pin uses native Hermes's strict lock: an unavailable model fails closed
rather than substituting another provider. **Hermes decides** releases that lock
and the previous raw model to Hermes's own session/default routing, including any
native `/model` override; it is not a promise of an automatic six-model optimizer.
Reported runtime metadata and fallback notices are kept separate from preference,
sanitized and logged; a response/failure never changes the saved selection.

Validation on 2026-10-08:

- Selector/voice/chat/approval/picker regression batch: **99 passed, 6 skipped**.
  Seven new Python cases cover both selector directions, stale values, adapter
  rebuild, unavailable-control writes, acknowledged display/history updates and
  a gateway-reported cloud fallback retaining the Hermes-routing preference.
- Wider source/shared-guard batch: **1180 passed, 7 skipped, 4 baseline failures**.
  Exact committed HEAD `87bca8d` under the same private environment: **1173 passed,
  7 skipped, the same 4 failures**. Two are the previously recorded order-dependent
  `society_browser` expectations. The other two are unchanged provider-list tests:
  an extra section-health tab and a public `~/.grok/auth.json` help path rejected by
  a blanket substring expectation. No test expectation or baseline was weakened.
- Real native Hermes runner: **2 passed**. Model preference tests use the actual
  plugin loader, adapter, DB persistence and runtime precedence. Reconstructing
  the DB adapter retains the selection. A stale override cannot shadow a confirmed
  pin; an unavailable fixture runtime and a foreign peer cannot alter it. Native
  detached-stop contract remains green. No model inference or real credentials.
- Frontend: **84 passed** across selector, remembered selection, reducer and
  WorkTrace components. TypeScript/production build passed. Light/dark browser
  previews of the actual selector show a chat change updating the voice label.
- Ruff comparison with exact HEAD: **32 existing findings, zero new findings**;
  whitespace checked. The generated production bundle is included.

Live local/cloud inference, actual gateway fallback, installed-app and voice-device
acceptance remain unverified for this stage. The broader connected-app bridge,
project intelligence/Orb/navigation and Windows installer acceptance remain open.
