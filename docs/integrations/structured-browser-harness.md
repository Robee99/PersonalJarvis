# Structured Browser Harness MCP

The optional `scripts/browser_harness_mcp.py` entry point extends the existing
MIT-licensed Browser Harness MCP server. Run it in the isolated Hermes Python
environment that already contains `browser-harness`; it adds no dependency to
the main desktop application's boot path and creates no second browser engine.

## Why this adapter exists

Small local models can corrupt quotes when generating Python containing
JavaScript. They can also read transitional DOM immediately after a click and
incorrectly describe an asynchronous update. Ordinary browser tasks should use
structured MCP arguments instead of `browser_exec` or model-authored scripts.

- `browser_snapshot`: bounded real page text, controls, labels, coordinates,
  observed selectors, and an optional selector count. Includes custom clickable
  controls such as pointer-enabled `div[data-testid]`, not just HTML buttons.
  Returns opaque references bound to the actual observed DOM nodes.
- `browser_click_ref`: accepts only a reference from the latest snapshot. It
  pins the node, validates its identity, text/value, visibility and hit target,
  consumes the reference before dispatch, then returns a fresh observed state.
  A stale or used reference cannot dispatch input. No model-authored CSS or
  postcondition is required. `verified: false` explicitly means dispatch alone
  does not prove task completion; inspect the real returned state or wait for a
  meaningful condition. There are no automatic action retries.
- `browser_wait_for_state`: waits for all requested text/count conditions.
  A timeout returns `verified: false` and the last real state.
- `browser_click_verified`: requires a postcondition before input, rejects
  ambiguous selectors, pins the selected DOM node across layout, checks that it
  remains connected, enabled, visible, and uncovered, and waits for the result.
  A condition already satisfied performs no action and reports
  `action_performed: false`, `transition_observed: false`.

The adapter does not replace browser permissions or approvals. Page content is
untrusted data. A verified condition establishes only that specific condition,
not successful completion of an unrelated task.

## Hermes configuration

Back up the existing configuration and update it through Hermes's atomic
configuration writer. Add an MCP server named `browser-harness` whose command is
the isolated Hermes Python executable and whose arguments are `-B` and the
absolute path to the adapter script. Use `BU_AUTOSPAWN=0` to prohibit automatic
cloud-browser creation. `BU_CDP_URL` can select an already approved local Chrome
debugging endpoint; it must not point to an unrelated copied profile.

For the JARVIS `api_server` surface, select `mcp-browser-harness` instead of the
code-execution `browser` toolset. Keep other surfaces and configured browser
fallbacks intact. Retain the structured navigation/tab/fill/scroll tools and the
snapshot, wait and reference-click tools; omit arbitrary `browser_js` and the
advanced `browser_click_verified` tool from the small-model allowlist. The latter
remains available for explicit developer workflows, but small models repeatedly
omitted its required postconditions and fabricated CSS. Invalid calls could then
trip Hermes's server-wide circuit breaker and block diagnostic snapshots too.
Restart an idle Hermes gateway through its supported lifecycle command and
confirm MCP discovery and health before testing.

## Verification and rollback

Focused tests: `tests/unit/test_browser_harness_adapter.py`. They cover quote-safe
selectors, delayed state transitions, timeouts, ambiguous/out-of-range input,
invalid expected selectors, already satisfied conditions, changed targets, and
reference clicks without model-authored selectors/postconditions.
Live verification should use a disposable page with a delayed update, a negative
state expectation, and a real read-only task through the running JARVIS model.
Do not count a tool-completed event as successful execution.

Rollback: restore the saved Hermes configuration using its supported writer and
restart the idle gateway. The adapter remains dormant when not configured; no
application data, original browser profile, or unrelated configuration needs to
be removed.
