/**
 * The brain's side of the live turn, fed by the `ConversationStateChanged`
 * bus event over the same WebSocket as `SystemStateChanged`
 * (`hooks/useWebSocket.ts`).
 *
 * Kept apart from the big event store on purpose: it changes on every tool
 * call of a run, and only the status surfaces (through
 * `hooks/useAssistantStatus.ts`) subscribe to it.
 *
 * COMPLETED clears the snapshot — a finished turn is not news. Every other
 * state replaces the previous one; `lib/assistantStatus.ts::turnIsFresh`
 * decides when an old snapshot stops counting, and the timer here makes the
 * surfaces re-render at that moment instead of on the next unrelated event.
 */
import { create } from "zustand";
import {
  parseConversationState,
  TURN_STATE_TTL_MS,
  type TurnSnapshot,
} from "@/lib/assistantStatus";

interface TurnStateStore {
  turn: TurnSnapshot | null;
  /**
   * A realtime transport is negotiating (`useVoiceMode().connecting`).
   *
   * That value is derived per hook instance from a REST poll, so letting every
   * surface call `useVoiceMode` would give each its own answer. Exactly one
   * writer mirrors it here — the sidebar, which is mounted for the whole life
   * of the main window — and every status surface reads the mirror.
   */
  realtimeConnecting: boolean;
  setRealtimeConnecting: (connecting: boolean) => void;
  /** Ingest one `ConversationStateChanged` payload; unreadable payloads are ignored. */
  ingest: (payload: unknown, at?: number) => void;
  /** Forget the turn (the socket reconnected: whatever it was is unknown now). */
  clearTurn: () => void;
  reset: () => void;
}

let expiry: ReturnType<typeof setTimeout> | null = null;

function clearExpiry(): void {
  if (expiry !== null) {
    clearTimeout(expiry);
    expiry = null;
  }
}

export const useTurnStateStore = create<TurnStateStore>((set, get) => ({
  turn: null,
  realtimeConnecting: false,
  setRealtimeConnecting: (connecting) => {
    if (get().realtimeConnecting !== connecting) set({ realtimeConnecting: connecting });
  },
  ingest: (payload, at = Date.now()) => {
    const next = parseConversationState(payload, at);
    if (!next) return;
    clearExpiry();
    if (next.state === "completed") {
      set({ turn: null });
      return;
    }
    set({ turn: next });
    const ttl = TURN_STATE_TTL_MS[next.state];
    expiry = setTimeout(() => {
      expiry = null;
      // Only drop the snapshot this timer was armed for.
      if (get().turn === next) set({ turn: null });
    }, ttl);
  },
  clearTurn: () => {
    clearExpiry();
    if (get().turn !== null) set({ turn: null });
  },
  reset: () => {
    clearExpiry();
    set({ turn: null, realtimeConnecting: false });
  },
}));
