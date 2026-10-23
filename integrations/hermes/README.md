# Hermes failed-turn replay repair

This integration contains a two-commit patch series for the existing Hermes
runtime. It does not replace the JARVIS application or its data.

## Source and scope

- Upstream: https://github.com/NousResearch/hermes-agent (MIT; retain the upstream license).
- Base: `8f817911723e3ec13e7f47fc022fdcc9e2149354`, version 0.21.7.
- First patch: `9f5b43853fe87aa644c5eb7f4eb1b2ac8d2c7156`.
- Final patch: `0f4b064101febb2476e62068ee04766e83678cc5`.
- Runtime branch: `codex/jarvis-failure-replay-20261023`.

The provider request projection removes exact synthetic failed-reply notices
from assistant examples. Original conversation rows, user messages, tool calls,
tool results and partial-failure warnings remain intact. Consecutive user
messages are merged only in the outgoing Chat Completions representation,
including multimodal content. Persistence metadata is removed from raw requests.
Canonical replay and database synchronization are unchanged.

## Applying to a compatible checkout

Review local changes first. Decompress `failed-turn-replay-20261023.patch.gz`,
then apply the mailbox series with `git am failed-turn-replay-20261023.patch`
only to the recorded base or a compatible checkout. Stop and resolve conflicts
rather than replacing local files. The
installed runtime has an unrelated browser-profile change that must be preserved.
Never push these local runtime commits to the official upstream without a
separate contribution review.

## Windows verification

The mandatory Hermes `scripts/run_tests.sh` runner passed 112 tests across seven
files, using an isolated source snapshot and test environment. Ruff and scoped
diff checks passed. An independent review checked canonical-history preservation,
raw/constructed request parity, tool-call preservation and multimodal merging.

The first live attempt exposed a marker-echo regression; the second commit fixes
it. A later check exposed a separate model-proxy adoption failure and a stopped
gateway. Successful unit checks alone do not establish end-to-end browser control.
Live application results must be recorded separately after observing tool receipts.

The final Windows check passed through the running JARVIS chat API, Hermes and
the local Qwen endpoint. Turn `8c8014d1beed4020a066664f3e94cc36` invoked
`mcp__playwright__browser_navigate` with `https://www.swiggy.com/instamart`.
The persisted tool result reported that exact page URL; the tool took 0.93 seconds
and the complete model/tool/model turn took 34.282 seconds. No cart, account,
terminal or form actions were requested or executed. The model supplied a page
title absent from the tool result, so that title is not verified. Hermes and Qwen
health remained successful afterward. The proxy reported 39,505 context tokens
in its configured 65,536-token window. Both fallback configuration keys were
disabled through Hermes's atomic writer in the default local-Qwen profile;
other profiles and credentials were preserved.

## Model proxy recovery

The companion JARVIS change `6e4af79310` teaches the existing proxy to recognize
surviving weights after a proxy restart. Adoption verifies the backend alias,
configured context and absence of another healthy model backend. It allocates no
new weights. Cold-start RAM guards remain active. Startup adoption failures are
logged and leave recovery endpoints available. Fourteen focused Windows tests
pass, including mismatched identity/context, conflicting backends and recovery.

Deployment preserves the installed proxy's existing memory limits and hardware
settings rather than replacing its entire file with a repository copy.

## Rollback

Drain the Hermes gateway with its supported update control before rolling back.
Revert the two runtime commits in reverse order on the repair branch, preserving
unrelated modifications, then relaunch with the original command and Hermes home.
Do not reset or delete conversation data. For the proxy, restore its saved
pre-adoption file while idle and restart only the proxy; retain the existing model
process and settings. Reverting the JARVIS source commit alone does not roll back
an independently deployed runtime.
