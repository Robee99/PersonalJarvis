import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

/**
 * The HUD v3 board: the orb with the primary controls under it in the centre,
 * the real chat in the right column, the memory orb in the left one — and
 * the controls doing what they say (talk = the voice call path, bound to the
 * chat on screen first; mic = the pipeline's mute toggle mirrored from the
 * bus). The secondary instruments and the chat's own stage are stubbed; each
 * has its own tests.
 */
vi.mock("@/components/layout/DockRail", () => ({ DockRail: () => <nav data-testid="dock" /> }));
vi.mock("@/components/deck/DeckActivityCards", () => ({
  OutputsCard: () => <section>outputs</section>,
  RunsCard: () => <section>runs</section>,
}));
vi.mock("@/components/deck/DeckSignalCards", () => ({
  ApiStatsCard: () => <section>api</section>,
  LiveCounter: () => <div>counter</div>,
  VitalsCard: () => <section>vitals</section>,
}));
vi.mock("@/components/deck/DeckTurnCard", () => ({ TurnCard: () => <section>turn</section> }));
vi.mock("@/components/deck/DeckLogCard", async () => {
  const actual = await vi.importActual<typeof import("@/components/deck/DeckLogCard")>(
    "@/components/deck/DeckLogCard",
  );
  return { ...actual, LogCard: () => <section>log</section> };
});
vi.mock("@/hooks/useWakeWord", () => ({
  useWakeWord: () => ({ config: { phrase: "Hey Nova" }, loading: false, error: null }),
}));
const requestVoiceCall = vi.fn(async () => ({ armed: true }));
const setTtsVolume = vi.fn(async (_v: number, _p: boolean) => {});
vi.mock("@/lib/voiceApi", () => ({
  requestVoiceCall: () => requestVoiceCall(),
  requestVoiceHangup: async () => ({ stopped: true }),
  fetchTtsVolume: async () => ({ volume: 0.8 }),
  setTtsVolume: (v: number, p: boolean) => setTtsVolume(v, p),
}));
vi.mock("@/components/MascotGigi", () => ({ MascotGigi: () => <div data-testid="mascot-gigi" /> }));
vi.mock("@/components/home/ChatStage", () => ({
  ChatStage: ({ tail }: { tail?: unknown }) => (
    <div data-testid="chat-stage">{tail ? "with tail" : "thread"}</div>
  ),
}));
vi.mock("@/components/deck/MemoryOrb3D", () => ({
  MemoryOrb3D: ({ className }: { className?: string }) => <div data-testid="memory-orb-3d" className={className} />,
}));
vi.mock("@/views/MemoryOrbView", () => ({
  MemoryOrbView: ({ initialMode, importOpen }: { initialMode?: string; importOpen?: boolean }) => (
    <div data-testid="memory-orb-view" data-mode={initialMode} data-import={importOpen ? "open" : "closed"} />
  ),
}));
vi.mock("@/hooks/useVoiceEngineDisplay", () => ({
  useVoiceEngineDisplay: () => ({ tier: "pipeline", providerId: "ollama", providerLabel: "Ollama", model: "qwen3" }),
}));

import { MissionDeckView } from "@/views/MissionDeckView";
import { useI18nStore } from "@/i18n";
import { useDeckStore } from "@/store/deck";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { useAgentChatStore } from "@/store/agentChat";

const posts: string[] = [];
const voiceBinds: string[] = [];

function stubFetch(muted = false) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") posts.push(url);
      const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
      if (url === "/api/voice/mute" && init?.method === "POST") return json({ requested: true });
      if (url === "/api/voice/mute") return json({ available: true, muted });
      if (url === "/api/brain-map/projects") return json({ projects: [], last_run: null });
      if (url === "/api/agent-chat/voice-chat" && init?.method === "PUT") {
        voiceBinds.push(String(init.body));
        return json({ session_id: "s-hud", fresh: false });
      }
      return new Response("{}", { status: 404 });
    }),
  );
}

function renderBoard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MissionDeckView />
    </QueryClientProvider>,
  );
}

describe("HUD v2 layout and controls", { timeout: 15_000 }, () => {
  beforeEach(() => {
    posts.length = 0;
    voiceBinds.length = 0;
    window.localStorage.clear();
    useAgentChatStore.setState({ activeSessionId: "s-hud" });
    useI18nStore.getState().setUi("en", { push: false });
    useDeckStore.getState().resetDeck();
    useDeckStore.setState({ micMuted: null });
    useHomeStore.getState().resetTranscript();
    useEventStore.setState({
      connected: true,
      voiceReady: true,
      wsWarming: false,
      voiceState: "idle",
      assistantName: "Nova",
      messages: [{ id: "m1", role: "assistant", content: "Hello.", ts: Date.now() }],
      thinkingSteps: [],
      chatThinking: false,
      activeSection: "deck",
      toasts: [],
    });
    useEventStore.getState().setTranscription("", true);
    useDeckStore.getState().openBoard();
    requestVoiceCall.mockClear();
    setTtsVolume.mockClear();
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  test("the orb and its controls hold the centre; the chat right, the memory orb left", async () => {
    stubFetch();
    renderBoard();
    const board = screen.getByTestId("deck-board");
    const orb = within(board).getByTestId("deck-orb");
    const controls = within(board).getByTestId("hud-controls");
    // The controls sit in the centre column, right under the orb's stage.
    expect(orb.closest(".order-1")).toBe(controls.closest(".order-1"));
    expect(within(controls).getByTestId("hud-talk").textContent).toContain("Talk");
    expect(within(controls).getByTestId("hud-state").getAttribute("data-phase")).toBe("ready");

    // The chat is the real one (AssistantChat → ChatStage), not a transcript.
    const right = screen.getByTestId("deck-slot-right-top");
    expect(within(right).getByTestId("hud-chat")).toBeTruthy();
    expect(await within(right).findByTestId("chat-stage")).toBeTruthy();
    expect(within(right).queryByTestId("hud-composer")).toBeNull();
    // The memory orb is on the board by default — no toggle to find first.
    const left = screen.getByTestId("deck-slot-left-top");
    expect(within(left).getByTestId("hud-memory")).toBeTruthy();
    expect(within(left).getByTestId("memory-orb-3d").className).not.toContain("hidden");
    // The heavy secondary instruments are off the board.
    expect(screen.queryByText("wiki")).toBeNull();
    expect(screen.queryByText("terminals")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("hud-mic").hasAttribute("disabled")).toBe(false));
  });

  test("Talk binds the chat on screen, then takes the voice call path; the state word follows the store", async () => {
    stubFetch();
    renderBoard();
    fireEvent.click(screen.getByTestId("hud-talk"));
    // The call continues the chat beside the orb: bound first, then started.
    await waitFor(() => expect(requestVoiceCall).toHaveBeenCalledTimes(1));
    expect(voiceBinds).toEqual([JSON.stringify({ session_id: "s-hud", voice_session_id: null })]);
    act(() => useEventStore.setState({ voiceState: "listening" }));
    expect(screen.getByTestId("hud-state").getAttribute("data-phase")).toBe("listening");
    expect(screen.getByTestId("hud-talk").textContent).toContain("End");
  });

  test("Mic posts the toggle and mirrors the pipeline's VoiceMuteChanged", async () => {
    stubFetch(false);
    renderBoard();
    const mic = screen.getByTestId("hud-mic");
    await waitFor(() => expect(mic.hasAttribute("disabled")).toBe(false));
    expect(mic.getAttribute("aria-pressed")).toBe("false");
    await act(async () => {
      fireEvent.click(mic);
    });
    expect(posts).toContain("/api/voice/mute");
    // The button does not guess: it flips when the bus says so.
    expect(mic.getAttribute("aria-pressed")).toBe("false");
    act(() => useDeckStore.getState().ingest("VoiceMuteChanged", { muted: true, source: "web.hud" }, Date.now()));
    expect(screen.getByTestId("hud-mic").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("Mic muted")).toBeTruthy();
  });

  test("Voice silences the assistant for the session only (never persisted)", async () => {
    stubFetch();
    renderBoard();
    const voice = screen.getByTestId("hud-voice");
    await act(async () => {
      fireEvent.click(voice);
    });
    expect(setTtsVolume).toHaveBeenCalledWith(0, false);
    expect(screen.getByTestId("hud-voice").getAttribute("aria-pressed")).toBe("true");
    await act(async () => {
      fireEvent.click(screen.getByTestId("hud-voice"));
    });
    expect(setTtsVolume).toHaveBeenLastCalledWith(0.8, false);
  });

  test("the full memory map — and its data import — open over the HUD and close back to it", async () => {
    stubFetch();
    renderBoard();
    fireEvent.click(screen.getByRole("button", { name: "Import data into memory" }));
    const map = await screen.findByTestId("memory-orb-view");
    expect(map.getAttribute("data-mode")).toBe("memory");
    expect(map.getAttribute("data-import")).toBe("open");
    // Still the HUD underneath: same section, the board stays mounted.
    expect(useEventStore.getState().activeSection).toBe("deck");
    expect(screen.getByTestId("deck-board")).toBeTruthy();
    fireEvent.click(screen.getByTestId("hud-full-map-close"));
    expect(screen.queryByTestId("hud-full-map")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Open the full memory map" }));
    expect((await screen.findByTestId("memory-orb-view")).getAttribute("data-mode")).toBe("projects");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("hud-full-map")).toBeNull();
  });
});
