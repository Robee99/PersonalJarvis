import { useMemo } from "react";
import { useEventStore } from "@/store/events";
import { useTurnStateStore } from "@/store/turnState";
import { deriveAssistantStatus, type AssistantStatus } from "@/lib/assistantStatus";

/**
 * The assistant's status — the ONE source every status surface reads: the
 * sidebar dot and line, the voice banner, the HUD header and its ring.
 *
 * See `lib/assistantStatus.ts` for the derivation and its priority order.
 * `now` is read at render: the turn store re-renders its subscribers when a
 * snapshot expires, so the freshness check never lags behind a stale state.
 */
export function useAssistantStatus(): AssistantStatus {
  const connected = useEventStore((s) => s.connected);
  const wsWarming = useEventStore((s) => s.wsWarming);
  const voiceReady = useEventStore((s) => s.voiceReady);
  const voiceUnavailable = useEventStore((s) => s.voiceUnavailable);
  const voiceState = useEventStore((s) => s.voiceState);
  const chatThinking = useEventStore((s) => s.chatThinking);
  const turn = useTurnStateStore((s) => s.turn);
  const realtimeConnecting = useTurnStateStore((s) => s.realtimeConnecting);
  return useMemo(
    () =>
      deriveAssistantStatus({
        connected,
        wsWarming,
        voiceReady,
        voiceUnavailable,
        voiceState,
        turn,
        chatThinking,
        realtimeConnecting,
        now: Date.now(),
      }),
    [connected, wsWarming, voiceReady, voiceUnavailable, voiceState, chatThinking, turn, realtimeConnecting],
  );
}
