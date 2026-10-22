import type React from "react";
import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ChatsSurface } from "@/views/ChatsSurface";
import { HomeView } from "@/views/HomeView";
import { AssistantChat } from "@/components/home/AssistantChat";
import { useHomeStore } from "@/store/home";
import { readHomeSurface } from "@/lib/homeSurface";
import { useEventStore } from "@/store/events";
import { useAgentChatStore } from "@/store/agentChat";

/**
 * The front page is the HUD (2026-10-12): chat, voice orb and memory orb in
 * one section, so `ChatsSurface` renders the mission deck (its own layout is
 * HudLayout.test). The chat inside it is still the ONE chat with a voice mode
 * (2026-10-01) — `AssistantChat`. What is under test here is that shell: it
 * opens on the chat, an agent's chat takes the column, voice mode swaps in
 * the spoken stage on its own page and hands it the way back to typing, and
 * inside the HUD (embedded) the thread stays on screen in voice mode with the
 * spoken turn as its tail. Both stages are stubbed; the composer's voice
 * button is ChatStage's own test.
 */
vi.mock("@/views/MissionDeckView", () => ({
  MissionDeckView: () => <div data-testid="hud" />,
}));
vi.mock("@/components/home/VoiceStage", () => ({
  VoiceStage: ({ onExit }: { onExit?: () => void }) => (
    <div data-testid="voice">
      <button type="button" data-testid="voice-mode-exit" onClick={onExit}>
        back
      </button>
    </div>
  ),
}));
vi.mock("@/components/home/ChatStage", () => ({
  ChatStage: ({ tail, compact }: { tail?: React.ReactNode; compact?: boolean }) => (
    <div data-testid="chat" data-compact={compact ? "true" : "false"}>
      chat{tail}
    </div>
  ),
}));
vi.mock("@/components/home/HomeAgentChat", () => ({
  default: ({ agentId }: { agentId: string }) => <div data-testid="agent-chat">{agentId}</div>,
}));

const STORAGE_KEY = "jarvis.home.surface.v2";

describe("ChatsSurface (the front page)", () => {
  afterEach(cleanup);

  it("is the HUD", () => {
    render(<ChatsSurface />);
    expect(screen.getByTestId("hud")).toBeTruthy();
  });
});

describe("the chat (HomeView / AssistantChat)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useHomeStore.setState({ surface: readHomeSurface(), agentChatId: null, freshVoicePending: false, continuedVoiceId: null, voiceSwitchStopping: false });
    useEventStore.setState({ activeSection: "chats", voiceState: "idle", activeKind: "voice", activeThreadId: null });
  });

  afterEach(cleanup);

  it("inside the HUD keeps the thread on screen in voice mode, the spoken turn as its tail", async () => {
    window.localStorage.setItem(STORAGE_KEY, "voice");
    useHomeStore.setState({ surface: readHomeSurface() });
    render(<AssistantChat embedded tail={<p data-testid="tail">said</p>} />);
    const chat = await screen.findByTestId("chat");
    expect(chat.getAttribute("data-compact")).toBe("true");
    expect(screen.getByTestId("tail").textContent).toBe("said");
    expect(screen.queryByTestId("voice")).toBeNull();
  });

  it("gives the page to an agent's chat picked in the sidebar, and back on a new chat", async () => {
    useHomeStore.getState().openAgentChat("agent-7");
    render(<HomeView />);
    expect((await screen.findByTestId("agent-chat")).textContent).toBe("agent-7");
    expect(screen.queryByTestId("chat")).toBeNull();
    const { startNewTextChat } = await import("@/lib/newChat");
    act(() => startNewTextChat());
    expect(await screen.findByTestId("chat")).toBeTruthy();
    expect(useHomeStore.getState().agentChatId).toBeNull();
  });

  it("opens on the chat by default, with nothing above it", async () => {
    render(<HomeView />);
    expect(await screen.findByTestId("chat")).toBeTruthy();
    expect(screen.queryByTestId("voice")).toBeNull();
    expect(screen.queryByTestId("voice-mode-exit")).toBeNull();
    expect(screen.getByTestId("home-view").getAttribute("data-surface")).toBe("chat");
  });

  it("shows voice mode when a wake-word call starts without a composer click", async () => {
    render(<HomeView />);
    expect(await screen.findByTestId("chat")).toBeTruthy();
    act(() => useHomeStore.getState().ingest("VoiceSessionStarted", {
      session_id: "wake-call", wake_keyword: "jarvis",
    }, 1));
    expect(screen.getByTestId("voice")).toBeTruthy();
    expect(screen.queryByTestId("chat")).toBeNull();
    expect(screen.getByTestId("home-view").getAttribute("data-surface")).toBe("voice");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("voice");

    // Returning to typing stays explicit; normal voice updates cannot undo it.
    fireEvent.click(screen.getByTestId("voice-mode-exit"));
    act(() => useHomeStore.getState().ingest("SystemStateChanged", {
      previous: "LISTENING", new_state: "THINKING",
    }, 2));
    expect(await screen.findByTestId("chat")).toBeTruthy();
  });

  it("keeps an agent's chat open when Jarvis starts speaking", async () => {
    useHomeStore.getState().openAgentChat("agent-7");
    render(<HomeView />);
    expect(await screen.findByTestId("agent-chat")).toBeTruthy();
    act(() => useHomeStore.getState().ingest("VoiceSessionStarted", { session_id: "call" }, 1));
    expect(screen.getByTestId("agent-chat").textContent).toBe("agent-7");
    expect(useHomeStore.getState().surface).toBe("chat");
  });

  it("stays blank after hangup, late timeline updates, and switching back to typing", async () => {
    const fetcher = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify({ providers: [], sessions: [], session_id: "old-chat" })));
    vi.stubGlobal("fetch", fetcher);
    try {
      useHomeStore.setState({ surface: "voice", continuedVoiceId: "archive" });
      useEventStore.setState({ activeKind: "voice", activeThreadId: "archive" });
      useHomeStore.getState().seedTranscript([{ id: "old", who: "user", text: "Old call", ts: 1 }]);
      render(<HomeView />);
      await act(async () => {
        useHomeStore.getState().ingest("VoiceSessionStarted", { session_id: "call" }, 1);
        useHomeStore.getState().ingest("VoiceSessionEnded", { session_id: "call", hangup_reason: "hotkey" }, 2);
      });
      act(() => useAgentChatStore.setState({ timeline: {
        ...useAgentChatStore.getState().timeline,
        items: [{ type: "user", id: "late", text: "Late stored turn", tsMs: 3, attachments: [] }],
      } }));
      expect(useHomeStore.getState().transcript).toEqual([]);
      expect(useHomeStore.getState().freshVoicePending).toBe(true);
      expect(useEventStore.getState().activeThreadId).toBeNull();
      expect(screen.getByTestId("voice")).toBeTruthy();
      fireEvent.click(screen.getByTestId("voice-mode-exit"));
      expect(await screen.findByTestId("chat")).toBeTruthy();
      expect(useAgentChatStore.getState().activeSessionId).toBeNull();
      expect(fetcher.mock.calls.some(([url, init]) => String(url) === "/api/agent-chat/voice-chat" && !init?.method)).toBe(false);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("ignores the retired Voice | Chat switch's stored choice", async () => {
    window.localStorage.setItem("jarvis.home.surface.v1", "voice");
    useHomeStore.setState({ surface: readHomeSurface() });
    render(<HomeView />);
    expect(await screen.findByTestId("chat")).toBeTruthy();
  });

  it("goes back to typing from voice mode and remembers it", async () => {
    window.localStorage.setItem(STORAGE_KEY, "voice");
    useHomeStore.setState({ surface: readHomeSurface() });
    render(<HomeView />);
    expect(screen.getByTestId("voice")).toBeTruthy();
    fireEvent.click(screen.getByTestId("voice-mode-exit"));
    expect(await screen.findByTestId("chat")).toBeTruthy();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("chat");
  });

  it("lands on the chat when the stored value is corrupt", async () => {
    window.localStorage.setItem(STORAGE_KEY, "garbage");
    useHomeStore.setState({ surface: readHomeSurface() });
    render(<HomeView />);
    expect(await screen.findByTestId("chat")).toBeTruthy();
  });
});
