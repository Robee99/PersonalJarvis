# ADR-0040 — Images stay on local route targets unless cloud vision is allowed; escalation only on request; NVIDIA NIM rejected

**Status:** Accepted (2026-10-03)
**Date:** 2026-10-03
**Reference:** [Final hardening evidence](../research/final-hardening-evidence.md) §7–§9; [implementation status](../personaljarvis-implementation-status.md)

## Context

With `[brain.route_policy]` on, the fast tier is Step 3.7 Flash. It is reached
through a loopback gateway, but it is a hosted model (Nous). It reads images:
measured on the owner's PC, it read a synthetic invoice exactly. Images could
still reach it, or other hosted models, by four paths:
- the vision lead chain and the tool-model hoist, which pulled targets from
  outside the tiers;
- a screenshot a tool took mid-turn, fed back to the running model;
- Computer Use, which sends each step's screenshot down the `fast` chain;
- Computer Use's last resort, which tried every registered vision provider.

None of these asked whether the target was local. A screenshot, camera frame
or dropped file could therefore reach a cloud model without the user knowing.

A failed deep turn could also escalate to Paperclip (Claude) without being
asked (`on_deep_failure`).

NVIDIA's free NIM endpoints were evaluated as a cloud vision fallback. They
fail three of the brief's six conditions:
- the terms are evaluation-only;
- the terms forbid uploading personal information;
- no local failure case they would fix has been shown.

## Decision

1. A route target declares `local = true` only when the model runs on this
   machine. A loopback gateway to a hosted model is not local.
2. On an image turn, the policy chain keeps only local targets unless
   `allow_cloud_vision = true`, which is explicit consent. That setting
   defaults to false, is visible in config and can be revoked by setting it
   back.
3. If no local target remains, Jarvis answers in the turn's language that the
   image stays on the device. It publishes a `blocked` route event with the
   excluded targets, and calls no model.
4. The tool-model hoist never adds a provider from outside the policy chain.
   A tool screenshot is shown only to a model that may receive images.
   Computer Use skips targets that may not, and its last resort stays inside
   the policy's tiers. If the policy cannot be read, nothing gets the image.
5. Escalation to Paperclip happens only on an explicit trigger phrase.
   `on_deep_failure` is removed; old configs still load and the key is ignored.
6. NVIDIA NIM is not added: no provider, adapter or configuration.

## Consequences

- With the policy on and no local deep model, image turns are answered without
  sending the image anywhere. The owner can opt in with `allow_cloud_vision`.
- The route event records each excluded target with the reason
  `cloud-vision-off`, so the decision can be audited from the flight recorder.
- Recovery lines (unavailable, timeout, cancelled, policy denied) are spoken in
  English, German or Spanish, following the turn's language.
