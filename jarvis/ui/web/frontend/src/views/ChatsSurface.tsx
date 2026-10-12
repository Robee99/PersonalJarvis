import { MissionDeckView } from "@/views/MissionDeckView";

/**
 * The front page: the HUD (2026-10-12).
 *
 * The owner wanted the chat, the HUD and the memory orb "all together in one
 * section" — so this one surface is all three: the 3D memory orb on the left,
 * the live voice orb and its controls in the middle, and the real chat (the
 * same agent-chat store, sessions and composer the Chat page used — typed
 * and spoken turns in one thread) on the right. The section ids "chats",
 * "deck" and "orb" all render it (MainView).
 *
 * The sidebar is the navigation here, so the deck's own dock stays out.
 */
export function ChatsSurface() {
  return <MissionDeckView dock={false} />;
}
