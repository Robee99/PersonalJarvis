# Webcam eyes and Screen Watch

The **Eyes** control opens two independent visual sources: **Webcam** shows the person at the camera; **Share screen** shows the display or window chosen in the system picker. Neither control requests audio. Both may run together. Stop either source independently, or use **Stop all**. Sources stop when the window closes or its chat changes.

This implements the visual organs described in the supplied Jarvis prompt pack, pages 10, 18 and 19, inside the existing Jarvis application. It does not add the pack's focus timer, tab locking, knowledge graph, persona or alternative server.

## Questions and voice

Select an image-capable local model in the Jarvis conversation. Its server must load the model's vision projector and publish image support in its model catalog. A model without declared image input remains unavailable for pictures. Current downloaded model and projector files are reused; the feature does not install models or packages.

While display sharing is active, a question uses a new JPEG frame from that share. “Look at me” and webcam/shirt/face questions use a new webcam frame. A request for both supplies separately labelled images. The existing agent-chat runner, BrainManager, dispatcher and vision provider handle the answer. A voice question in the bound chat requests a new frame from that same browser window through the existing event channel. Requests expire rather than reuse old pixels.

Frames are bounded, transient inputs. They are not saved as chat attachments, recordings or database payloads. Source labels and timestamps are validated. Per-turn image slots are cleared after completion or cancellation. Screen privacy restrictions are retained: a disabled screen-context setting, denylist, or custom sensitive-pattern rule blocks live display sharing; use the existing filtered appshot path in those cases.

Answers and notices use Jarvis's existing speech announcement path, without starting a microphone. `?mute=1` suppresses vision speech for tests. The normal chat still shows the answer. A stopped source or missing frame produces an honest error.

## Screen Watch

**Watch** reuses the selected display stream and requires **Entire Screen**. A tab or window remains usable for manual questions but cannot claim whole-screen watch. The local thumbnail comparison runs every five seconds. A change resets stillness; sixty seconds without a change allows one image-grounded nudge, followed by a three-minute cooldown. Busy conversations wait instead of creating overlapping turns. Visual nudges use the selected local model, with action tools disabled and no paid fallback.

An always-on-top desktop face card makes watch visible outside the app. Its short, renewable lease expires if the frontend disappears. Separate window leases prevent one window's Stop from hiding another window's active indicator. The card uses the existing native indicator sidecar and never arms computer control or a global Escape binding.

**Quiet 3 min**, “give me a minute”, and “I need to do something important” pause nudges for three minutes while retaining the visual sources. Late watch output is withheld when the source is stopped or the conversation is quiet.

## Optional local posture tracking

The webcam itself and question-time webcam images do not require landmark tracking. When all of the following files already exist in Jarvis's local `holo/vendor` directory, the browser can also run local face and pose landmarks:

- `vision_bundle.mjs` and its existing `wasm` runtime
- `face_landmarker.task`
- `pose_landmarker_lite.task`

The capability endpoint reports missing files and does not download them. Missing assets leave webcam questions available, with an explicit tracking-unavailable status. No image is sent to a model to invent posture signals.

Local signals use 700 ms sustained head-down/slouch posture, a 30-second cooldown, and a 12-second absence grace. The geometry thresholds are heuristics requiring physical calibration. Camera frames used for these landmarks stay in the browser. Focus-session integration is deliberately outside this vision-only change.

## Verification limits

Automated evidence covers independent media lifecycles, late permission responses, fresh-frame ownership, session isolation, cancellation, bounded image intake, privacy gates, local-model routing, watch/relief timing, React controls and native indicator leases. The native card was rendered offscreen in light and dark modes. Physical camera quality, screen-picker behavior, spoken playback and macOS Spaces behavior still need device acceptance; no live user webcam or desktop image was captured during automated checks.

The local face and pose task files were absent on the development host, so actual landmark inference is unavailable there under the no-install constraint. The initial vision baseline also has two existing failures in `test_manager_screen_context.py`; they are retained and compared rather than suppressed.
