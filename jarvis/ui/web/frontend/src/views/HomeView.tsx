import { useHomeStore } from "@/store/home";
import { AssistantChat } from "@/components/home/AssistantChat";

/**
 * The chat on a page of its own — what the "chats" section showed until the
 * HUD became the front page (2026-10-12). The HUD now mounts the same
 * `AssistantChat` in its right column (`embedded`, see HudChatPanel); this
 * full-page shell stays for the chat's own tests and any surface that wants
 * the chat without the HUD around it.
 *
 * Since 2026-10-01 it is ONE chat with the assistant, with a voice mode
 * inside it (components/home/AssistantChat): the maintainer retired the
 * separate `Voice | Chat` faces — "only a normal chat, with a button that
 * turns voice mode on". Typed and spoken turns still reach the same
 * assistant through the same event bus; `data-surface` says which mode the
 * chat is in (store/home.ts).
 */
export function HomeView() {
  const surface = useHomeStore((s) => s.surface);
  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="home-view" data-surface={surface}>
      <AssistantChat />
    </div>
  );
}
