import { useCallback } from "react";
import { useVoiceCall } from "@/components/agentic/useVoiceCall";
import { bindVoiceChat } from "@/lib/agentChatApi";
import { useAgentChatStore } from "@/store/agentChat";
import { useEventStore } from "@/store/events";

/**
 * The HUD's one talk press, shared by every Stark screen: the microphone
 * button in the command bar and the reactor in the middle do the same thing.
 *
 * Nothing listens until the person presses. A call that starts here
 * continues the chat on screen: the backend is told which chat it is first
 * (`bindVoiceChat`, the same binding the chat's own voice button makes), so
 * the spoken turns are filed into that thread and the call starts with its
 * history. A failed start (no microphone, permission refused, no voice
 * pipeline) comes back as a toast from `useVoiceCall`, never silently.
 */
export function useHudTalk(onPress?: () => void) {
  const call = useVoiceCall();
  const connected = useEventStore((s) => s.connected);
  const { active, busy, connecting, toggleCall } = call;

  const press = useCallback(() => {
    onPress?.();
    if (active || busy || connecting || !connected) {
      void toggleCall();
      return;
    }
    void bindVoiceChat(useAgentChatStore.getState().activeSessionId)
      .catch((err: unknown) => {
        // The call still starts; it just falls back to the newest chat.
        console.info("Voice chat binding failed.", err);
      })
      .finally(() => void toggleCall());
  }, [active, busy, connecting, connected, onPress, toggleCall]);

  return {
    ...call,
    connected,
    press,
    /** The press would do nothing useful right now. */
    disabled: busy || connecting || !connected,
  };
}
