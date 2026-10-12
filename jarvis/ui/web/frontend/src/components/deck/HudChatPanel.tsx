import { useEffect, useRef } from "react";
import { MessagesSquare, SquarePen } from "lucide-react";
import { DeckCard, DeckIconButton } from "@/components/deck/DeckCard";
import { HudVoiceTail, hasVoiceTail, useHudVoiceTail } from "@/components/deck/HudTranscriptPanel";
import { AssistantChat } from "@/components/home/AssistantChat";
import { useVoiceCall } from "@/components/agentic/useVoiceCall";
import { fetchVoiceChat } from "@/lib/agentChatApi";
import { startNewTextChat } from "@/lib/newChat";
import { useAgentChatStore } from "@/store/agentChat";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { useT } from "@/i18n";

/**
 * The chat on the HUD — the real one (2026-10-12).
 *
 * Not a second chat: this is the front page's own conversation
 * (`AssistantChat` → `ChatStage`, the `jarvis` agent-chat store), with its
 * history, its sessions (the sidebar's recent chats open here) and its full
 * composer. Typed turns go out through that composer; spoken turns are filed
 * into the same chat by the backend when they end, and while one is still
 * being spoken it shows as the thread's tail (`HudVoiceTail`) — so there is
 * ONE list of what was said, typed or spoken, not a transcript beside a chat.
 *
 * A call started from a blank chat gets its own chat on the backend; once
 * the call ends, that chat is opened here so the turns just spoken stay on
 * screen.
 */
export function HudChatPanel({ className }: { className?: string }) {
  const t = useT();
  const voiceState = useEventStore((s) => s.voiceState);
  const agentChatId = useHomeStore((s) => s.agentChatId);
  const view = useHudVoiceTail();
  const tail = hasVoiceTail(view);
  const live = Boolean(view.partial || view.streaming) || voiceState === "listening";
  useOpenChatAfterCall();

  return (
    <DeckCard
      icon={MessagesSquare}
      title={t("hud3.chat.title")}
      meta={view.partial ? t("hud2.transcript.hearing") : view.streaming ? t("hud2.transcript.replying") : undefined}
      live={live}
      variant="rail"
      actions={<DeckIconButton icon={SquarePen} label={t("hud3.chat.new")} onClick={startNewTextChat} />}
      className={className}
      bodyClassName="flex min-h-0 flex-col p-0"
    >
      <div data-testid="hud-chat" data-agent={agentChatId ?? undefined} className="flex min-h-0 flex-1 flex-col">
        <AssistantChat embedded tail={tail ? <HudVoiceTail view={view} /> : null} />
      </div>
    </DeckCard>
  );
}

/**
 * When a call ends with no chat open, open the chat the backend filed it
 * into — the same lookup voice mode's "back to typing" does.
 */
function useOpenChatAfterCall() {
  const { active, connecting } = useVoiceCall();
  const live = active || connecting;
  const wasLive = useRef(live);
  useEffect(() => {
    const ended = wasLive.current && !live;
    wasLive.current = live;
    if (!ended || useAgentChatStore.getState().activeSessionId) return;
    // The last turn is filed a moment after the hangup; give it that moment.
    const id = window.setTimeout(() => {
      void fetchVoiceChat()
        .then(({ session_id: sessionId }) => {
          const chat = useAgentChatStore.getState();
          if (!sessionId || chat.activeSessionId) return;
          chat.openSession(sessionId);
          void chat.loadSessions();
        })
        .catch((err: unknown) => {
          console.info("Voice chat lookup failed.", err);
        });
    }, 600);
    return () => window.clearTimeout(id);
  }, [live]);
}
