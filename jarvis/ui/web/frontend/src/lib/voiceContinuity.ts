import type { VoiceEngineDisplay } from "@/lib/voiceEngineDisplay";

/**
 * How the typed chat's provider pick relates to the engine that answers a
 * spoken turn — the one rule behind the notice on Jarvis' Voice tab.
 *
 * The typed chat keeps a per-chat provider and model (SQLite, the composer's
 * picker); voice reads `[voice].mode` plus either the realtime pick or the
 * pipeline brain. Showing "Hermes Agent" above the chat and then answering a
 * spoken turn on Gemini Live, with nothing on screen saying so, read as the
 * app swapping the person's choice for a paid provider behind their back.
 * Nothing here switches anything: it classifies, and the notice explains and
 * offers the transition the person can click.
 *
 *   - "same"       voice already runs on the chat's provider (pipeline brain).
 *   - "switchable" the chat runs on a provider API Jarvis' own brain can drive,
 *                  so Pipeline voice (speech-to-text → that brain →
 *                  text-to-speech) can use it after an explicit click.
 *   - "cli"        the chat runs a vendor CLI one-shot (Hermes Agent, Claude
 *                  Code, Codex …): it answers once, when it has finished, and
 *                  approves its own tools, so it cannot be the voice brain.
 *   - "none"       no explicit chat pick (the chat follows Jarvis' brain).
 */

export interface ChatPick {
  /** Agent-chat provider row id ("hermes", "openrouter", "local-openai" …). */
  id: string;
  label: string;
  /** "api" / "brain", or a vendor CLI runner ("hermes-cli", "claude-cli" …). */
  runner: string;
  /** "" = the provider's default model. */
  model: string;
}

export type VoiceContinuity =
  | { kind: "none" }
  | { kind: "same"; chat: ChatPick }
  | { kind: "switchable"; chat: ChatPick; needsPipeline: boolean }
  | { kind: "cli"; chat: ChatPick };

/** Runners Jarvis' own brain drives; everything else is a vendor CLI. */
const BRAIN_RUNNERS: ReadonlySet<string> = new Set(["api", "brain"]);

export function chatRunsOnBrain(runner: string): boolean {
  return BRAIN_RUNNERS.has((runner || "").trim().toLowerCase());
}

export function resolveVoiceContinuity(
  chat: ChatPick | null,
  engine: Pick<VoiceEngineDisplay, "tier" | "providerId">,
): VoiceContinuity {
  if (!chat || !chat.id) return { kind: "none" };
  if (!chatRunsOnBrain(chat.runner)) return { kind: "cli", chat };
  if (engine.tier === "pipeline" && engine.providerId === chat.id) {
    return { kind: "same", chat };
  }
  return { kind: "switchable", chat, needsPipeline: engine.tier === "realtime" };
}
