import { useState, type FormEvent } from "react";
import { ArrowUp, MessageSquareText } from "lucide-react";

import { sendChatMessage } from "@/lib/chat";
import { useEventStore } from "@/store/events";

/**
 * Text command entry for the Mission Deck. It uses the same WebSocket,
 * persisted conversation thread, optimistic event and timeout path as Chats.
 */
export function DeckChatInput() {
  const connected = useEventStore((state) => state.connected);
  const thinking = useEventStore((state) => state.chatThinking);
  const [value, setValue] = useState("");
  const [sending, setSending] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const content = value.trim();
    if (!content || !connected || sending) return;

    setSending(true);
    try {
      if (await sendChatMessage(content)) setValue("");
    } finally {
      setSending(false);
    }
  };

  return (
    <form
      aria-label="Send a text command to JARVIS"
      data-testid="deck-chat-composer"
      onSubmit={(event) => void submit(event)}
      className="flex w-full max-w-[30rem] items-center gap-2 rounded-lg border border-primary/25 bg-background/70 px-3 py-2 text-left shadow-lg shadow-black/20 backdrop-blur-xl"
    >
      <MessageSquareText aria-hidden="true" className="h-4 w-4 shrink-0 text-primary/70" />
      <input
        aria-label="Message JARVIS"
        autoComplete="off"
        className="min-w-0 flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground/70"
        disabled={!connected || sending}
        onChange={(event) => setValue(event.target.value)}
        placeholder={connected ? "Type a command…" : "Waiting for the JARVIS link…"}
        value={value}
      />
      <button
        aria-label="Send message"
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-35"
        disabled={!connected || sending || !value.trim()}
        type="submit"
      >
        <ArrowUp aria-hidden="true" className="h-4 w-4" />
      </button>
      <span className="sr-only" aria-live="polite">
        {thinking ? "JARVIS is responding" : ""}
      </span>
    </form>
  );
}
