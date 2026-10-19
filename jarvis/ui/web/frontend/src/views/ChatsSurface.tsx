import { MissionDeckView } from "@/views/MissionDeckView";

/**
 * The front page: the HUD's Command screen.
 *
 * The chat, the voice and the memory orb live on one screen (owner,
 * 2026-10-12): the reactor in the middle answers a press, the command bar
 * under it talks and types into the real chat (the same agent-chat store,
 * sessions and composer the Chat page used — typed and spoken turns in one
 * thread), and the memory orb opens from the rail. The section ids "chats",
 * "deck" and "orb" all render it (MainView).
 */
export function ChatsSurface() {
  return <MissionDeckView />;
}
