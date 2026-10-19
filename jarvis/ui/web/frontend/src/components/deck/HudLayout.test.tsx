import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

/**
 * The Command screen: the reactor in the centre over the map, system status
 * and quick access on the left, resources and tasks on the right, the command
 * bar under the reactor and the rail down the left — and the controls doing
 * what they say (talk = the voice call path, bound to the chat on screen
 * first; mic = the pipeline's mute toggle mirrored from the bus; the field =
 * a message into the HUD's chat). The instruments and the chat's own stage
 * are stubbed; each has its own tests.
 */
vi.mock("@/components/layout/DockRail", () => ({ DockRail: () => <nav data-testid="dock" /> }));
vi.mock("@/components/deck/DeckActivityCards", () => ({
  OutputsCard: () => <section>outputs</section>,
  RunsCard: () => <section>runs</section>,
}));
// The Stark instruments poll their own endpoints; each has its own tests.
vi.mock("@/components/stark/StarkInstruments", () => ({
  SystemStatusPanel: () => <section>status</section>,
  ResourcesPanel: () => <section>vitals</section>,
  QuickAccess: () => <nav>quick</nav>,
  StarkClock: () => <span>clock</span>,
}));
vi.mock("@/components/stark/ContextMeter", () => ({
  ContextMeter: () => <section>context</section>,
  useDeckContext: () => ({ data: undefined }),
  fmtK: (n: number) => String(n),
}));
vi.mock("@/components/stark/WeatherGlobe", () => ({
  WeatherGlobe: () => <section>weather</section>,
  WeatherMini: () => <section>weather</section>,
  readCity: () => null,
}));
vi.mock("@/components/stark/TasksPanel", () => ({ TasksPanel: () => <section>tasks</section> }));
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

describe("Command screen layout and controls", { timeout: 15_000 }, () => {
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

  test("the reactor holds the centre; status and quick access left, resources and tasks right", async () => {
    stubFetch();
    renderBoard();
    const board = screen.getByTestId("deck-board");
    expect(within(screen.getByTestId("stark-halo")).getByTestId("deck-orb")).toBeTruthy();
    const left = within(board).getByTestId("deck-slot-left-top");
    expect(within(left).getByText("status")).toBeTruthy();
    expect(within(left).getByText("quick")).toBeTruthy();
    const right = within(board).getByTestId("deck-slot-right-top");
    expect(within(right).getByText("vitals")).toBeTruthy();
    expect(within(right).getByText("tasks")).toBeTruthy();
    // The command bar sits under the reactor, the rail down the left edge.
    expect(within(board).getByTestId("stark-command-bar")).toBeTruthy();
    const rail = screen.getByTestId("stark-rail");
    for (const key of ["command", "intelligence", "orbital", "world", "memory", "agents", "voice", "settings"]) {
      expect(within(rail).getByTestId(`stark-rail-${key}`)).toBeTruthy();
    }
    expect(screen.getByTestId("stark-rail-command").getAttribute("aria-current")).toBe("page");
    // The header: the name as a wordmark, the clock, the three tools.
    expect(screen.getByTestId("stark-wordmark").textContent).toBe("NOVA");
    expect(screen.getByTestId("stark-clock")).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId("stark-mic-mute").hasAttribute("disabled")).toBe(false));
  });

  test("Talk binds the chat on screen, then takes the voice call path; the state line follows the store", async () => {
    stubFetch();
    renderBoard();
    // Nothing listens before the button is pressed.
    expect(requestVoiceCall).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("stark-talk"));
    await waitFor(() => expect(requestVoiceCall).toHaveBeenCalledTimes(1));
    expect(voiceBinds).toEqual([JSON.stringify({ session_id: "s-hud", voice_session_id: null })]);
    act(() => useEventStore.setState({ voiceState: "listening" }));
    expect(screen.getByTestId("stark-bar-state").textContent).toMatch(/Listening/);
  });

  test("Mic posts the toggle and mirrors the pipeline's VoiceMuteChanged", async () => {
    stubFetch(false);
    renderBoard();
    const mic = screen.getByTestId("stark-mic-mute");
    await waitFor(() => expect(mic.hasAttribute("disabled")).toBe(false));
    expect(mic.getAttribute("aria-pressed")).toBe("false");
    await act(async () => {
      fireEvent.click(mic);
    });
    expect(posts).toContain("/api/voice/mute");
    // The button does not guess: it flips when the bus says so.
    expect(mic.getAttribute("aria-pressed")).toBe("false");
    act(() => useDeckStore.getState().ingest("VoiceMuteChanged", { muted: true, source: "web.hud" }, Date.now()));
    expect(screen.getByTestId("stark-mic-mute").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("stark-bar-state").textContent).toBe("Microphone off");
  });

  test("Voice silences the assistant for the session only (never persisted)", async () => {
    stubFetch();
    renderBoard();
    await act(async () => {
      fireEvent.click(screen.getByTestId("stark-voice-mute"));
    });
    expect(setTtsVolume).toHaveBeenCalledWith(0, false);
    expect(screen.getByTestId("stark-voice-mute").getAttribute("aria-pressed")).toBe("true");
    await act(async () => {
      fireEvent.click(screen.getByTestId("stark-voice-mute"));
    });
    expect(setTtsVolume).toHaveBeenLastCalledWith(0.8, false);
  });

  test("a typed message goes into the HUD's chat; a failed send says so and keeps the text", async () => {
    stubFetch();
    const send = vi.fn(async (_text: string) => "sent" as const);
    useAgentChatStore.setState({ send } as never);
    renderBoard();
    const field = screen.getByTestId("stark-ask") as HTMLInputElement;
    expect(screen.getByTestId("stark-send").hasAttribute("disabled")).toBe(true);
    fireEvent.change(field, { target: { value: "  status report  " } });
    await act(async () => {
      fireEvent.submit(field.closest("form")!);
    });
    expect(send).toHaveBeenCalledWith("status report");
    expect(field.value).toBe("");

    send.mockImplementationOnce(async () => "failed" as never);
    fireEvent.change(field, { target: { value: "again" } });
    await act(async () => {
      fireEvent.submit(field.closest("form")!);
    });
    expect(screen.getByTestId("stark-send-failed")).toBeTruthy();
    expect(field.value).toBe("again");
  });

  test("the chat button opens the whole thread over the screen and closes it again", () => {
    stubFetch();
    renderBoard();
    const drawer = screen.getByTestId("stark-chat-drawer");
    expect(drawer.className).toContain("hidden");
    fireEvent.click(screen.getByTestId("stark-chat-toggle"));
    expect(drawer.className).not.toContain("hidden");
    fireEvent.click(screen.getByTestId("stark-chat-toggle"));
    expect(drawer.className).toContain("hidden");
  });

  test("Memory on the rail opens the full memory map over the HUD; Escape closes back to it", async () => {
    stubFetch();
    renderBoard();
    fireEvent.click(screen.getByTestId("stark-rail-memory"));
    const map = await screen.findByTestId("memory-orb-view");
    expect(map.getAttribute("data-mode")).toBe("memory");
    // Still the HUD underneath: same section, the board stays mounted.
    expect(useEventStore.getState().activeSection).toBe("deck");
    expect(screen.getByTestId("deck-board")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("hud-full-map")).toBeNull();
  });
});
