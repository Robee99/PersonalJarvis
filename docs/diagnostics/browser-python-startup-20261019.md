# Windows managed-browser runtime correction — 2026-10-19

## Change

Parent HEAD: `50a7f30e05a64235f5fcc0f190182b173206e4bd`.
Branch: `codex-gemma-mcp-fix-20261014`.

During a real, isolated headless source startup, the backend reached its ready
state but automatic managed-browser setup failed. The app runs Python 3.11.15;
the installer reused that interpreter even though the checked-in Windows lock
pins `numpy==2.5.3`, whose package metadata requires Python >=3.12. The captured
pip exception explicitly rejected the installed interpreter.

The correction changes only the minimum reusable host interpreter from 3.11 to
3.12. Python 3.11 applications now use the existing isolated managed Python 3.12
bootstrap. Source hosts on 3.12/3.13 retain their fast path; frozen applications
and the Windows ARM emulation request retain their managed-runtime paths.
Dependency hashes, package pins, installer approval boundaries and atomic
promotion of the previously verified runtime remain unchanged.

Files:

- `jarvis/society/browser/install.py`
- `tests/unit/society/test_browser_install.py`
- `tests/integration/test_managed_browser_runtime.py`
- This evidence note.

## Verification

1. Added interpreter-selection regressions before the source fix: Python 3.11
   failed the managed-interpreter assertion; the other five cases passed.
2. Final focused suite: **24 passed**, zero skipped/failed in **7.98 seconds**.
   Includes installer repair/promotion, shutdown, pointer behavior and the real
   opt-in managed-browser acceptance test.
3. Ran the real corrected installer in a new temporary per-user directory:
   Python **3.12.13** was selected from the Python **3.11.15** application host.
   Full locked dependency installation, Chromium setup and the actual renderer
   probe passed in **72.95 seconds**. Browser-Use **0.13.10** imported successfully.
4. The actual isolated browser rendered local HTML, filled an input, read its
   value back, captured a PNG and verified the image. The promoted installation
   manifest recorded the real managed executable, browser version and packages.
5. Restarted the actual source backend against the verified isolated runtime;
   `/api/health`, `/api/tools`, `/api/mcps` and `/api/tasks` all returned HTTP 200.
   Health reported `version=2.7.1`, `instance=dev`, `ok=true`; tool listing
   contained 138 tools. MCP had zero servers by explicit isolation configuration,
   so this is not an MCP execution test.
6. Both diagnostic headless processes shut down with exit code zero. Their
   localhost listener on 47922 was absent afterward. The corrected run logged
   bootstrap socket readiness at **217.7 ms**; that measures the bootstrap,
   not full application initialization or model response latency.
7. Ruff and scoped diff checks passed. Independent read-only review found no
   important issues in the fix or the new opt-in runtime acceptance test.

The suite emitted two existing Starlette/httpx/anyio deprecation warnings.
No paid model call, inference, user browser interaction or desktop clicking was
performed. The browser install was per-user and isolated, with no system
libraries installed. No main application dependencies were changed.

## Evidence and reproduction

Artifacts:
`%TEMP%\jarvis-startup-probe-20261019\`

- `startup.log`: original reproduced browser dependency failure.
- `browser-real-install.log`: successful real install, timing and selected Python.
- `corrected-startup.log`: post-fix isolated backend readiness and shutdown.
- `corrected-api-results.json`: actual four-endpoint responses.
- `final-results.xml`: final 24-test result.

Verified browser data:
`%TEMP%\jb-1019\society\browser\installed.json`.

The opt-in acceptance test runs with `JARVIS_BROWSER_ACCEPTANCE_ROOT` set to a
separate test data directory. With that variable absent, it skips; it does not
silently install anything during the ordinary test suite. Use the repository
virtual environment and disable pytest's configured report output when running
ad hoc tests so the user's existing `report.xml` is preserved.

## Deployment limits and rollback

This fix is TESTED in an isolated Windows browser runtime and source backend.
It is not deployed to the packaged application or verified through Qwen/Hermes
MCP. Production port 47821 was not started. Voice was explicitly off in the
headless tests; no voice or God's Eye View success is claimed. The previously
recorded installed/source lineage mismatch remains unresolved. The old packaged
TaskStack database-lock error did not occur with the fresh isolated source data;
that does not prove the existing production database is repaired.

Existing installed apps, user settings, credentials, production databases,
models and staged God's Eye View files were preserved. Rollback is a reviewed
revert of this four-file source commit from a clean tree; do not reset the shared
working tree. No production configuration needs restoring. The temporary
acceptance runtime can remain as evidence and does not replace a working runtime.
