/**
 * The ONE derivation of "what is the assistant doing right now?".
 *
 * Before this module the sidebar dot, the voice-warming banner, the HUD header
 * and the HUD ring each derived a state of their own from overlapping store
 * fields, so the HUD could say READY in its header while the banner said the
 * assistant was still starting (forensic UI audit 2026-10-07, P0-1). Every
 * status surface now reads `deriveAssistantStatus` (through
 * `hooks/useAssistantStatus.ts`), so two surfaces can never disagree: there is
 * exactly one phase at a time, and "ready" is only ever produced when the voice
 * stack really is ready.
 *
 * Inputs:
 * - the socket (`connected`, `wsWarming`),
 * - voice boot readiness (`voiceReady`, `voiceUnavailable`),
 * - the microphone side of the turn (`voiceState`, from `SystemStateChanged`),
 * - the brain side of the turn (`turn`, from `ConversationStateChanged`): only
 *   the brain knows it is ACTING with a tool, was INTERRUPTED, CANCELLED by
 *   "stop", or RESUMED by "continue".
 *
 * Pure: no React, no store — the hook feeds it and the tests drive it directly.
 */
import type { VoiceState } from "@/store/events";

/** Mirror of `jarvis/core/events.py::ConversationStateChanged.state`, lowercased. */
export type TurnState =
  | "listening"
  | "thinking"
  | "acting"
  | "speaking"
  | "interrupted"
  | "cancelled"
  | "resumed"
  | "completed";

export const TURN_STATES: readonly TurnState[] = [
  "listening",
  "thinking",
  "acting",
  "speaking",
  "interrupted",
  "cancelled",
  "resumed",
  "completed",
];

export function isTurnState(value: unknown): value is TurnState {
  return typeof value === "string" && (TURN_STATES as readonly string[]).includes(value);
}

/** The brain's latest word on the current turn. */
export interface TurnSnapshot {
  state: TurnState;
  /** Short, user-safe label from the backend (a tool name); "" when none. */
  detail: string;
  sessionId: string;
  /** Wall-clock ms when the event arrived. */
  at: number;
}

/**
 * Parse a `ConversationStateChanged` payload into a snapshot.
 *
 * Returns null for a payload this client cannot read (unknown state, wrong
 * shape) — the status then simply keeps its previous value instead of
 * inventing one.
 */
export function parseConversationState(payload: unknown, at: number): TurnSnapshot | null {
  if (!payload || typeof payload !== "object") return null;
  const p = payload as { state?: unknown; detail?: unknown; session_id?: unknown };
  if (typeof p.state !== "string") return null;
  const state = p.state.toLowerCase();
  if (!isTurnState(state)) return null;
  const detail = typeof p.detail === "string" ? p.detail.trim().slice(0, 80) : "";
  const sessionId = typeof p.session_id === "string" ? p.session_id : "";
  return { state, detail, sessionId, at };
}

/**
 * How long a turn state may stand without a newer event before the status
 * stops believing it.
 *
 * - The stopped states are a hint ("say continue"), not a condition: the
 *   backend keeps the checkpoint for 30 minutes, but a status line still
 *   saying "Stopped" an hour after a stop is stale news.
 * - The working states normally end with COMPLETED / INTERRUPTED. The ceiling
 *   only protects against a lost terminal event leaving "Acting" up forever;
 *   it is generous because a real tool run can take minutes.
 */
export const TURN_STATE_TTL_MS: Readonly<Record<TurnState, number>> = {
  listening: 120_000,
  thinking: 600_000,
  acting: 600_000,
  speaking: 300_000,
  resumed: 600_000,
  interrupted: 120_000,
  cancelled: 120_000,
  completed: 0,
};

/** Whether `turn` still says something about the present at `now`. */
export function turnIsFresh(turn: TurnSnapshot | null, now: number): turn is TurnSnapshot {
  if (!turn) return false;
  const ttl = TURN_STATE_TTL_MS[turn.state];
  return ttl > 0 && now - turn.at < ttl;
}

export type AssistantPhase =
  | "offline"
  | "booting"
  | "error"
  | "listening"
  | "acting"
  | "speaking"
  | "thinking"
  | "resumed"
  | "interrupted"
  | "cancelled"
  | "connecting"
  | "starting"
  | "unavailable"
  | "paused"
  | "ready";

/** The colour a status may carry: ok (green), busy (green, moving), warn, error, muted. */
export type StatusTone = "ok" | "busy" | "warn" | "error" | "muted";

/** Where the voice stack itself stands, independent of the current turn. */
export type VoiceAvailability = "offline" | "booting" | "starting" | "unavailable" | "ready";

export interface AssistantStatus {
  phase: AssistantPhase;
  tone: StatusTone;
  /** i18n key of the status label (`assistant_status.*` / `voice_state.*`). */
  labelKey: string;
  /** Extra text appended to the label ("web_search"), "" when none. */
  detail: string;
  /** Show a spinner instead of a dot. */
  spinner: boolean;
  /** Pulse the dot. */
  pulse: boolean;
  /** The voice stack can hear and speak right now. */
  voice: VoiceAvailability;
  /** Anything but a connected, warmed-up assistant at rest. */
  hasNews: boolean;
}

export interface AssistantStatusInputs {
  connected: boolean;
  wsWarming: boolean;
  voiceReady: boolean;
  voiceUnavailable: boolean;
  voiceState: VoiceState;
  turn: TurnSnapshot | null;
  /** A typed chat turn is waiting for its reply (the store's `chatThinking`). */
  chatThinking?: boolean;
  /** A realtime transport is negotiating (useVoiceMode().connecting). */
  realtimeConnecting?: boolean;
  now: number;
}

function voiceAvailability(i: AssistantStatusInputs): VoiceAvailability {
  if (!i.connected) return i.wsWarming ? "booting" : "offline";
  if (i.voiceReady) return "ready";
  return i.voiceUnavailable ? "unavailable" : "starting";
}

function status(
  phase: AssistantPhase,
  tone: StatusTone,
  labelKey: string,
  voice: VoiceAvailability,
  opts: { detail?: string; spinner?: boolean; pulse?: boolean } = {},
): AssistantStatus {
  return {
    phase,
    tone,
    labelKey,
    detail: opts.detail ?? "",
    spinner: opts.spinner ?? false,
    pulse: opts.pulse ?? false,
    voice,
    hasNews: phase !== "ready",
  };
}

/**
 * Resolve the single phase, highest priority first:
 *
 * 1. no socket (booting / offline) — nothing else can be true without one;
 * 2. a voice error;
 * 3. the microphone is listening — the person must know they are heard;
 * 4. the brain is ACTING (with the tool's name);
 * 5. speaking, then resumed / thinking (a typed turn waiting counts too);
 * 6. a stopped turn ("say continue");
 * 7. a realtime transport negotiating;
 * 8. the voice stack still starting, or given up (typing still works);
 * 9. paused, else ready.
 *
 * "ready" is therefore unreachable while the voice stack is starting: READY
 * and STARTING cannot be shown at the same time by construction.
 */
export function deriveAssistantStatus(i: AssistantStatusInputs): AssistantStatus {
  const voice = voiceAvailability(i);
  if (voice === "booting") return status("booting", "warn", "voice_state.booting", voice, { spinner: true });
  if (voice === "offline") return status("offline", "muted", "voice_state.offline", voice);
  if (i.voiceState === "error") return status("error", "error", "voice_state.error", voice);

  const turn = turnIsFresh(i.turn, i.now) ? i.turn : null;
  const t = turn?.state;

  if (i.voiceState === "listening" || t === "listening") {
    return status("listening", "busy", "voice_state.listening", voice, { pulse: true });
  }
  if (t === "acting") {
    return status("acting", "busy", "assistant_status.acting", voice, {
      detail: turn?.detail ?? "",
      pulse: true,
    });
  }
  if (i.voiceState === "speaking" || t === "speaking") {
    return status("speaking", "busy", "voice_state.speaking", voice, { pulse: true });
  }
  if (t === "resumed") return status("resumed", "busy", "assistant_status.resumed", voice, { pulse: true });
  if (i.voiceState === "thinking" || t === "thinking" || i.chatThinking) {
    return status("thinking", "busy", "voice_state.thinking", voice, { pulse: true });
  }
  if (t === "interrupted") return status("interrupted", "warn", "assistant_status.interrupted", voice);
  if (t === "cancelled") return status("cancelled", "warn", "assistant_status.cancelled", voice);

  if (i.realtimeConnecting || i.voiceState === "connecting") {
    return status("connecting", "warn", "voice_state.connecting", voice, { spinner: true, pulse: true });
  }
  if (voice === "starting") return status("starting", "warn", "voice_state.starting", voice, { spinner: true });
  if (voice === "unavailable") return status("unavailable", "muted", "voice_state.unavailable", voice);
  if (i.voiceState === "paused") return status("paused", "muted", "voice_state.paused", voice);
  return status("ready", "ok", "voice_state.idle", voice);
}

/** Label + detail as one line ("Acting · web_search"). */
export function formatAssistantStatus(s: AssistantStatus, t: (key: string) => string): string {
  const label = t(s.labelKey);
  return s.detail ? `${label} · ${s.detail}` : label;
}

/** Theme-token classes for the status dot (light AND dark through the tokens). */
export const STATUS_DOT_CLASS: Readonly<Record<StatusTone, string>> = {
  ok: "bg-success",
  busy: "bg-success",
  warn: "bg-warning",
  error: "bg-destructive",
  muted: "bg-muted-foreground",
};
