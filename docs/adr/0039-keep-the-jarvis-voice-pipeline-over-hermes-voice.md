# ADR-0039 — Keep the Jarvis voice pipeline; Hermes Voice Mode and voice frameworks are not adopted as the runtime

**Status:** Accepted (2026-10-03)
**Date:** 2026-10-03
**Reference:** [Final hardening evidence](../research/final-hardening-evidence.md) §3–§6; ADR-0037

## Context

The final hardening brief asked whether Hermes Voice Mode, or one small set of
open-source repositories, could remove most of the remaining custom voice
code while keeping the Brain abstraction, the local tool boundary, local-first
privacy, Step/Qwen routing and the explicit Claude/Paperclip boundary.

Hermes Agent 0.21.3 (MIT) was read from its installed source on the owner's
PC:
- Voice mode is a push-to-talk CLI feature.
- Turn end is an RMS threshold with a fixed 3.0 s auto-stop.
- Barge-in interrupts Hermes's own agent.
- The default TTS is Microsoft's online `edge` voice.
- The transcript goes into Hermes's own `chat()` loop. There is no interface
  for an external brain.

pipecat (BSD-2) and livekit-agents (Apache-2.0, plus a separate model licence
on its turn detector) are full runtimes with their own event model; livekit
also has its own tool executor. PersonalJarvis already has, in `jarvis/speech`
and `jarvis/audio` (about 36k lines):
- a 7-state turn-taking machine;
- layered VAD and endpointing;
- wake word, echo guard and device recovery;
- sentence-streamed TTS with fallback;
- cancellation that reaches tools and Paperclip.

## Decision

1. The PersonalJarvis voice pipeline stays the voice runtime.
2. Hermes Voice Mode, pipecat, livekit-agents, RealtimeSTT/TTS and TEN are not
   adopted. None of them removes PJ code without replacing the Brain or the
   tool boundary.
3. Smart Turn v3.2, already vendored in `jarvis/voice_engine/turn.py`, is the
   candidate to shorten the classic pipeline's fixed 1.5 s end-of-turn wait.
   Wiring it waits for on-device recordings to tune the threshold, including
   for German.

## Consequences

- No second voice runtime, event model or tool executor exists.
- Hermes stays what it is on the PC today: a separate agent with its own
  voice CLI, reachable as a Paperclip agent.
- Improvements to the voice layer happen inside the existing pipeline and are
  measured on the device (the built-in microphone currently blocks that).
