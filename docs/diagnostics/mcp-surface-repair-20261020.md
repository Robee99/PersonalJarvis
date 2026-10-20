# Connected MCP tool selection repair: 2026-10-20

Branch: `codex/voice-text-repair-20261020`.

## Confirmed failure

The installed HUD 3.1.1 runs local-openai / qwen3-vl-8b with a 65,536-token
window and a 48-tool request cap. The user's reported chat has seven turns
but only two genuine tool calls: navigation and the society capability
catalog. Both claimed Windows App launches exist only in assistant prose.
No App launch receipt exists for those claims.

The live registry contains 290 tools. Eight configured MCP servers are
running with no registry error, including God's Eye View, Filesystem and
Windows Desktop. The model's request log specifically hides
`gods-eye-view/show_in_gods_eye_view` on the user's "world panel" request.
It instead preserves internal panel/read tools. The false "[used tool ...]"
prose imitates the history annotation format, which is not an invocation.

## Repair

- `jarvis/brain/manager.py`: match natural screen/browser vocabulary to
  connected tool descriptions and retain `tools-list` when combined token
  and tool-count budgets permit. Mandated actions and model limits keep
  priority. No new permission is granted and the context remains 65,536.
- `jarvis/brain/tool_use_loop.py`: hold a leading historical tool marker out
  of chat/speech, never convert it into arguments or a call, allow one
  bounded corrective model round, then report an error if the fabrication
  repeats. Respect elapsed deadlines, budgets and closed stream consumers.
  Literal/instructional requests for that syntax are exempt.
- `tests/unit/brain/test_mcp_surface_incident.py`: exact incident reproductions
  and reviewer-found count-cap/deadline/consumer regressions.

## Verification

- Initial incident reproduction: four failures, one pass before the fix.
- Three additional reviewer edge cases also failed before correction.
- Final focused tool-loop, context, chat, verification and deadline tests:
  **96 passed**.
- Required broader contracts plus affected tests before the final edge fixes:
  **656 passed, one pre-existing failure**. The unchanged failure is
  `test_router_tools_is_pure_dispatcher_set`, whose expected set omits the
  existing Cognee/KEPT entries; previously reproduced on exact HUD base.
- Exact-base Ruff comparison: no added finding identities. The manager has
  28 distinct legacy finding signatures; the loop has six. New test lint
  and scoped whitespace checks pass.
- Independent review found three issues; all were reproduced, corrected
  and reviewed again with no remaining findings.
- Real selection against the actual 290-tool catalog keeps the globe view
  tool for the user's exact request, Windows App for "open chrome", and
  inventory discovery within the 48-tool cap.
- Genuine authenticated calls through the live app's normal supervisor and
  MCP gateway succeeded for Filesystem allowed directories and Windows
  DisplayInventory. These are real read-only executions, not model-generated
  acknowledgements. No clicking or desktop UI automation was used.

The installed gateway's slashless `/api/control/mcp` currently returns HTTP
307. Its supported `/api/control/mcp/` endpoint initializes, discovers 301
outbound tools and executes both read-only checks. This transport difference
is separate from the native Qwen request-selection failure. The prior
canonical-route source repair remains a subsequent port; no authentication
or redirect protection was bypassed for these tests.

## Windows package and activation boundary

The current installed executable's normalized code matches the clean branch
baseline for both replaced modules. The prepared incremental artifact
replaces exactly those two modules and preserves 13,787 other module blobs,
the current HUD bundle, native dependencies, credentials and configuration.

An isolated frozen development launch returned health 200 / version 3.1.1 /
instance dev after 4.61 seconds, then its own process tree was closed.
The existing production app was not terminated by that check.

The staged transaction waits for normal closure of the production app,
verifies original, backup and artifact hashes, atomically replaces only the
two executables and reopens the same installed path. At documentation time
it is **waiting for app exit**, so the new model behavior is not yet verified
in the running Windows application. Read the transaction's `apply-status.json`
for the actual subsequent activation result; do not infer it from a build.

Artifacts and execution evidence:
`C:/Users/rober/Documents/Codex/2026-10-12/referenced-chatgpt-conversation-this-is-an/outputs/mcp-surface-repair-20261020/`.

Rollback: close JARVIS normally, restore `PersonalJarvis.exe` and `jarvis.exe`
from `%LOCALAPPDATA%/Jarvis/backups/mcp-surface-20261020/` into the same install
directory, then reopen the usual app. All user data and server configuration
remain in place. This restores the already applied voice/text repair.
