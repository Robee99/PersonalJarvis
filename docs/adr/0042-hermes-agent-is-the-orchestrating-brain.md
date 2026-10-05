# ADR-0042 — Hermes Agent is the orchestrating brain; Jarvis is the voice and safety front-end

**Status:** Accepted (2026-10-05)
**Date:** 2026-10-05
**Supersedes:** the "Hermes stays a separate agent" consequence of ADR-0039
**Reference:** `jarvis/plugins/brain/hermes.py`; `tests/unit/brain/test_hermes_brain.py`, `tests/unit/brain/test_hermes_session.py`

## Context

The owner decided on 2026-10-05 that one agent orchestrates everything:

    user -> Jarvis (voice, UI) -> Hermes Agent -> Hermes picks the model
         (local Qwen, local Gemma, a free cloud model) -> Hermes tools and agents
         -> Jarvis -> user

ADR-0039 kept the Jarvis voice pipeline and left Hermes a separate agent behind
Paperclip. The voice half of that decision still holds. The Hermes half does
not.

Hermes Agent's gateway exposes an API server (`hermes gateway` with
`API_SERVER_ENABLED`, loopback port 8642). Its runs API (`POST /v1/runs`,
events from `/v1/runs/{id}/events`) runs a full Hermes agent turn and
provides:
- per-run model and provider selection, and the provider and model that
  actually served the run (`run.completed.runtime`);
- `tool.started` / `tool.completed` and `subagent.*` events;
- `approval.request` events, resolved with `POST /v1/runs/{id}/approval`;
- `POST /v1/runs/{id}/stop`;
- session continuation with `session_id`: Hermes loads the history itself,
  and delegated work finishes in the background and lands in the session
  transcript.

The OpenAI-compatible chat-completions endpoint was used first. Measured on
the owner's PC on 2026-10-05:
- without a session id, every turn started a new Hermes session (Hermes
  answered "what did you just do?" with "nothing" after 24 tool calls);
- the installed Hermes version sends no approval events there, so a
  computer-use step that needed approval stopped and never reached the user.

## Decision

1. The `hermes` brain plugin sends every Jarvis turn, voice or text, to Hermes
   as one run.
   - It sends no Jarvis tools and does not pick the model.
   - The card value `""` means Hermes decides.
   - `alias` names a Hermes `model_routes` entry.
   - `provider::model` names one Hermes provider (`local-qwen::qwen`,
     `local-gemma::gemma-4-12b-qat`).
2. Every run continues one Hermes session (`session_id: jarvis-main`).
   - Jarvis sends only the newest turn, because Hermes holds the history.
   - Jarvis does not rebuild the conversation.
3. With Hermes as the brain, `BrainManager` stands its own orchestration down
   (`orchestrates_tools`): local actions, skills, wiki ingest, screen looks,
   Agentic-IDE, forced spawns, the unsupported-intent refusal and tool
   mandates.
4. Voice cannot reach a second brain.
   - With Hermes as the brain, realtime voice (Gemini Live, the local voice
     engine) and the ChatGPT subscription voice profile stand down.
   - Voice always runs through the speech pipeline into the same
     `BrainManager`.
5. Approvals stay Hermes's own.
   - Jarvis speaks the approval question while the run waits.
   - The user's next answer resolves that same run:
     - "yes" runs the command once;
     - "no" denies it;
     - an unclear answer is asked again;
     - unrelated words deny it before the new turn starts.
   - Jarvis never approves on its own.
6. When Hermes delegates work (`delegate_task`), Jarvis watches the session
   transcript for the result. When the result lands, Jarvis asks Hermes about
   it in the same session and speaks the answer as an announcement.

## The boundary

| Jarvis | Hermes |
|---|---|
| Microphone, wake word, VAD, STT, TTS, barge-in | Model and provider choice, fallback |
| Turn taking; stopping the run when the user cuts in | Reasoning, planning, tool and skill selection |
| Speaking approval questions and passing the answer back | Approval policy and the decision gate |
| Honesty guard: no "done" without a Hermes tool event | Terminal, files, browser, computer use, GitHub, MCP, memory, delegation |
| Paid-provider deny list, loopback-only Hermes, key read in place | Session history, compression, background delegation |

## Consequences

- Jarvis has no second agent loop, router or tool executor while Hermes is the
  brain.
- Jarvis's own tool-risk tiers do not apply to Hermes's tools. Safety for those
  is Hermes's approval mode, which Jarvis surfaces by voice.
- What Hermes can do is what its `api_server` platform enables. Its default
  toolset leaves out `computer_use` (`platform_toolsets.api_server` or
  `hermes tools enable computer_use --platform api_server`). That toolset also
  needs the cua-driver on Windows.
- The front page's chat is the same brain. With Hermes as the brain, its
  Hermes seat runs on the brain runner (`BrainManager.generate`), so a typed
  turn joins the voice's Hermes session. Before this, that seat ran a
  one-shot `hermes -z` with its own session and the trimmed German persona
  prompt, and answered in German (measured on the owner's PC, 2026-10-05).
- Two Hermes surfaces stay outside this session because the user starts them
  explicitly: a Hermes seat in the Agentic IDE chat and a Hermes mission
  worker. Both run `hermes -z` in their own session. One-shot mode approves
  Hermes's own tool prompts, and a mission contains them in its worktree.
- The live voice proof waits on a working microphone on the owner's PC
  (ADR-0039 and the status document).
