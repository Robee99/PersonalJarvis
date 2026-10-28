# Jarvis runtime architecture (2026-10-07)

How a voice OS on Hermes Agent ships: one brain, one conversation, a thin
real-time shell, and a release judged by what it does on the user's machine.
Authority: ADR-0042. Evidence for the current state is in
`JARVIS_THREE_WAY_ARCHITECTURE_AUDIT_2026-10-07.md`.

## The shape

```text
mic ─ wake ─ VAD ─ STT ─┐                         ┌─ sentence TTS ─ speaker
                        ├─ reflexes (no model) ───┤
typed chat (front page) ┘        │                └─ chat transcript (same turn)
                                 ▼
                 Hermes run API, session `jarvis-main`
                 (model routing, tools, skills, MCP, memory,
                  browser, computer use, vision, delegation, code)
                                 │
           approvals ──► spoken / on-screen yes-no ──► /v1/runs/{id}/approval
           barge-in / stop ───────────────────────► /v1/runs/{id}/stop
```

Background work (wiki curation, learning review, `/goal` verification,
scheduled tasks, provider tests) runs in the Hermes session
`jarvis-background`, never in the user's conversation.

## Who owns what

| Layer | Owner | In this repo |
|---|---|---|
| Microphone, wake word, VAD, STT | Jarvis | `jarvis/speech/` |
| Reflexes: brightness (read back from Windows), navigation, stop | Jarvis | `jarvis/platform/brightness.py`, `BrainManager` reflex block |
| Turn transport, streaming, approvals, stop | Jarvis → Hermes | `jarvis/plugins/brain/hermes.py` |
| Everything an agent does | Hermes | Hermes Agent config and toolsets |
| TTS, barge-in, UI, approval presentation | Jarvis | `jarvis/speech/`, `jarvis/ui/` |
| Memory knowledge (imported notes, Obsidian) | Jarvis wiki, read by Hermes over MCP | `jarvis/memory/wiki/`, `mcp_servers.jarvis` |

## Rules the code now enforces (this change set)

1. **One conversation.** Voice and the front-page chat each hold a
   `HermesBrain`, both joined to `jarvis-main`, and share one pending
   approval: an approval asked by voice can be answered by typing, and the
   reverse. Every other `HermesBrain` starts in `jarvis-background`.
2. **One brain.** While Hermes is the brain the front-page chat offers only the
   Hermes seat; an older chat on another seat is moved onto Hermes when it is
   next used, and a new chat on another seat opens on Hermes.
3. **No second router.** No Jarvis fallback chain sits behind Hermes; Hermes's
   own `fallback_providers` (free-only) decide.
4. **No second voice.** The flash acknowledgement model is not built while
   Hermes is the brain; acknowledgements are canned lines.
5. **Hermes acts on the computer.** `jarvis system free-voice` installs
   Hermes's computer-use driver when missing and enables the `computer_use`
   toolset for the API server Jarvis talks to.

## Latency budgets

| Path | Budget | Measured by |
|---|---|---|
| Reflex (brightness, stop) | ≤ 2 s to the spoken answer | `jarvis system acceptance` → `brightness` |
| Conversational turn | ≤ 5 s to first text (median) | `jarvis system acceptance` → `latency` |
| Interrupt | turn ends ≤ 5 s after stop | `jarvis system acceptance` → `interrupt` |

The known cause of slow turns on the PC (99k–207k-token turns because
`jarvis-main` never compacted) is addressed by free-voice's context cap
(`compression.threshold_tokens 40000`); the acceptance run is how that is
confirmed.

## Still in the tree, not on the Hermes path

These remain for installs that do not use Hermes and are the deletion list for
the next step (each goes through the ADR-0042 import and test search first):

* realtime voice brains (Gemini Live, Vertex, OpenAI realtime/live, the local
  voice engine's own LLM); `voice.mode` still defaults to `realtime` until
  free-voice switches it;
* the vendor-CLI and API seats on the front-page chat (hidden while Hermes is
  the brain);
* the legacy fallback chain in `BrainManager` (bypassed while Hermes is the
  brain).

## How a release is judged

`jarvis system acceptance` drives the running app through typed and spoken
turns, web and browser tasks, an interrupted delegation, Notepad and the
screen, imported-memory recall and a brightness change read back from
Windows, and writes a PASS/FAIL scorecard with timings to
`%LOCALAPPDATA%\Jarvis\acceptance\`. The checks that need a person (wake word,
camera, hearing the voice, barge-in by speaking) are listed in
`JARVIS_FINAL_ACCEPTANCE_MATRIX_2026-10-07.md`.
