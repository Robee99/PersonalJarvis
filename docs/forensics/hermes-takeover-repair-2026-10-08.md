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
