import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  Sidebar,
  SIDEBAR_DEFAULT_WIDTH,
  SIDEBAR_RAIL_AT_WIDTH,
  SIDEBAR_RAIL_WIDTH,
} from "@/components/layout/Sidebar";
import { NAV_GROUPS, NAV_FOOTER_ITEMS, SETTINGS_HUB_IDS } from "@/components/layout/navGroups";
import { isSectionId, useEventStore } from "@/store/events";
import { useQuickSwitcher } from "@/store/quickSwitcher";
import { useQuickSwitchSettings } from "@/store/quickSwitchSettings";
import { useHomeStore } from "@/store/home";
import { useIdeChatStore } from "@/store/ideChat";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useSettingsPage } from "@/store/settingsPage";
import { sectionPrefetch } from "@/lib/sectionPrefetch";
import { SECTION_PREFETCH_DELAY_MS } from "@/hooks/useSectionPrefetch";

test.each([SIDEBAR_DEFAULT_WIDTH, SIDEBAR_RAIL_WIDTH])(
  "sidebar intent warms a destination without navigating (width=%s)",
  (width) => {
    vi.useFakeTimers();
    const warm = vi.spyOn(sectionPrefetch, "prefetch").mockResolvedValue();
    try {
      useEventStore.setState({ activeSection: "chats" });
      renderSidebar(width);
      expect(warm).not.toHaveBeenCalled();
      fireEvent.mouseEnter(screen.getByTestId("nav-primary-agents"));
      act(() => { vi.advanceTimersByTime(SECTION_PREFETCH_DELAY_MS); });
      expect(warm).toHaveBeenCalledExactlyOnceWith("agents");
      expect(useEventStore.getState().activeSection).toBe("chats");
      fireEvent.focus(screen.getByTestId("sidebar-profile-toggle"));
      act(() => { vi.advanceTimersByTime(SECTION_PREFETCH_DELAY_MS); });
      expect(warm).toHaveBeenLastCalledWith("profile");
      fireEvent.click(screen.getByTestId("nav-primary-agents"));
      expect(useEventStore.getState().activeSection).toBe("agents");
    } finally {
      cleanup();
      warm.mockRestore();
      vi.useRealTimers();
    }
  },
);

test("IDE rail keeps workspace options and Jarvis Live reachable", () => {
  act(() => {
    useEventStore.setState({ activeSection: "agentic-ide" });
    useIdeProjectsStore.setState({ activeWorkspaceId: null, action: null });
  });
  renderSidebar(SIDEBAR_RAIL_WIDTH);
  expect(screen.queryByTestId("ide-project-tree")).toBeNull();
  const options = screen.getByRole("button", { name: "Workspace options" }) as HTMLButtonElement;
  expect(options.disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Jarvis Live" }));
  expect(useIdeProjectsStore.getState().action?.kind).toBe("toggle-voice");
  act(() => useIdeProjectsStore.setState({ activeWorkspaceId: "w1" }));
  expect(options.disabled).toBe(false);
  fireEvent.click(options);
  expect(useIdeProjectsStore.getState().action).toMatchObject({ kind: "workspace-options", workspaceId: "w1" });
  cleanup();
});

test("IDE sidebar puts Projects first and returns to the normal chat navigation", async () => {
  act(() => useEventStore.setState({ activeSection: "agentic-ide" }));
  renderSidebar();
  expect(await screen.findByTestId("ide-project-tree")).toBeDefined();
  // The agents list lives in the IDE's right-hand side panel now.
  expect(screen.queryByTestId("ide-workspace-agents")).toBeNull();
  expect(screen.queryByTestId("sidebar-new-chat")).toBeNull();
  expect(screen.queryByTestId("nav-row-agentic-ide")).toBeNull();
  // No "Back to Jarvis" row: the caption's back arrow leaves the IDE.
  expect(screen.queryByTestId("ide-back-to-jarvis")).toBeNull();
  act(() => useEventStore.setState({ activeSection: "chats" }));
  expect(screen.getByTestId("sidebar-new-chat")).toBeDefined();
  cleanup();
});

// The sidebar header avatar must mirror the chosen on-screen display style:
// the ghost mascot ONLY when the user explicitly picked "mascot"; the slim bar
// for "jarvis_bar"/"none" and while the style is still loading (config null).
// Mock the overlay-style hook so the test controls the style without a fetch.
const overlayMock = vi.hoisted(() => ({ style: "jarvis_bar" as string | null }));
vi.mock("@/hooks/useOverlayStyle", () => ({
  useOverlayStyle: () => ({
    config: overlayMock.style
      ? { style: overlayMock.style, options: ["jarvis_bar", "mascot", "none"] }
      : null,
    loading: false,
    error: null,
    refetch: () => {},
    saveStyle: () => {},
  }),
}));

// useVoiceMode fetches /api/settings/voice-mode; mock it so the footer card's
// pipeline-vs-realtime split is driven by the test, not a fetch. The default
// mirrors a fresh pipeline install (the pre-existing footer tests rely on it).
const voiceModeMock = vi.hoisted(() => ({
  value: {
    mode: "pipeline",
    activeProvider: null as string | null,
    activeProviderLabel: null as string | null,
    activeModel: null as string | null,
    sessionActive: false,
    activeSessionMode: null as "pipeline" | "realtime" | null,
    activeSessionProvider: "",
    activeSessionModel: "",
  },
}));
vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => voiceModeMock.value,
}));

function renderSidebar(width?: number) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <Sidebar width={width} />
    </QueryClientProvider>,
  );
}

describe("Sidebar voice header", () => {
  beforeEach(() => {
    useEventStore.setState({
      voiceState: "idle",
      transcription: "",
      transcriptionFinal: true,
      connected: true,
    });
  });

  afterEach(() => {
    cleanup();
  });

  test("does not render the floating mascot bubble while listening", () => {
    // The mascot's listening speech-bubble is anchored to the left of the
    // mascot (right: calc(100% + 10px)). In the sidebar the mascot sits flush
    // against the window edge, so the bubble slides off-screen and only its
    // yellow border + glow bleed back in — the spurious "yellow frame" the
    // user reported. The sidebar must not render that bubble.
    useEventStore.setState({
      voiceState: "listening",
      transcription: "auflegen",
      transcriptionFinal: false,
    });

    const { container } = renderSidebar();

    expect(container.querySelector(".gigi-bubble-listening")).toBeNull();
    expect(container.querySelector(".gigi-bubble")).toBeNull();
  });

  test("no longer shows the live transcription — that lane moved to the voice stage", () => {
    // Since 2026-08-23 the transcript is read on the front page itself
    // (components/home/VoiceStage), where it has the room to be read; the
    // sidebar box would be a second, cramped copy of the same words.
    useEventStore.setState({
      voiceState: "listening",
      transcription: "auflegen",
      transcriptionFinal: false,
    });

    renderSidebar();

    expect(screen.queryByText("auflegen")).toBeNull();
  });

  test("starts with New chat without duplicate voice controls", () => {
    renderSidebar();
    expect(screen.queryByTestId("home-surface-switch")).toBeNull();
    expect(screen.getByTestId("sidebar-new-chat")).toBeTruthy();
  });
});

// New chat must let users choose either surface without changing the current
// conversation until they make a choice.
describe("Sidebar new-conversation button", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      // GET /api/chats answers with a list, the agent-chat sessions probe with
      // an empty roster; every other call the mounted sidebar fires gets a
      // harmless object. An undefined list here crashes the recent-chats block.
      vi.fn(async (url: string) =>
        String(url).startsWith("/api/chats")
          ? new Response(JSON.stringify([]), { status: 200 })
          : new Response(JSON.stringify({ sessions: [] }), { status: 200 }),
      ),
    );
    useEventStore.setState({
      connected: true,
      activeSection: "board",
      conversations: [],
      messages: [],
      activeThreadId: "old-voice-thread",
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    // The event store is module-global: leaving a fetched-in list behind
    // would break whichever test file runs next.
    useEventStore.setState({ conversations: [], messages: [], activeThreadId: null });
  });

  test("opens an empty typed chat in one click, even from voice mode", async () => {
    useHomeStore.setState({ surface: "voice", transcript: [] });
    renderSidebar();
    const button = screen.getByTestId("sidebar-new-chat");
    expect(button.textContent).toContain("New chat");
    await act(async () => { button.click(); await Promise.resolve(); });
    expect(useHomeStore.getState().surface).toBe("chat");
    expect(useEventStore.getState().activeSection).toBe("chats");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("never starts a voice run — voice is a mode inside the chat now", async () => {
    useHomeStore.setState({ surface: "chat", transcript: [] });
    renderSidebar();
    await act(async () => { screen.getByTestId("sidebar-new-chat").click(); await Promise.resolve(); });
    expect(useHomeStore.getState().surface).toBe("chat");
    const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls;
    expect(calls.some((c) => String(c[0]) === "/api/chats/voice/new")).toBe(false);
  });
});

describe("Sidebar header avatar", () => {
  beforeEach(() => {
    useEventStore.setState({
      voiceState: "idle",
      transcription: "",
      transcriptionFinal: true,
      connected: true,
      voiceReady: true,
      assistantName: "Ruben",
    });
  });

  afterEach(() => {
    cleanup();
    overlayMock.style = "jarvis_bar";
  });

  // The mark is the user's pet (Gigi by default), drawn by PetMark from the
  // pets answer — never the old static Gigi image.
  test("renders the pet mark, not the old Gigi image", () => {
    // The mark heads the identity row, which returns when the quick
    // switcher (and with it the search bar) is switched off.
    useQuickSwitchSettings.setState({ enabled: false });
    const { container } = renderSidebar();
    useQuickSwitchSettings.setState({ enabled: true });
    const avatar = container.querySelector('[data-testid="sidebar-style-avatar"]');
    expect(avatar).not.toBeNull();
    expect(avatar?.getAttribute("data-variant")).toBe("logo");
    expect(avatar?.querySelector('[data-testid="pet-mark"]')).toBeTruthy();
    expect(avatar?.querySelector("img")).toBeNull();
  });
});

describe("Sidebar settings-hub entry", () => {
  beforeEach(() => {
    useEventStore.setState({
      voiceState: "idle",
      transcription: "",
      transcriptionFinal: true,
      connected: true,
      voiceReady: true,
      activeSection: "chats",
    });
  });

  afterEach(() => {
    cleanup();
  });

  test("the profile button opens the hub on the Profile tab", () => {
    // The popup is gone: one click lands in the Settings hub, on Profile.
    renderSidebar();

    fireEvent.click(screen.getByTestId("sidebar-profile-toggle"));

    expect(useEventStore.getState().activeSection).toBe("profile");
  });

  test("the SETTINGS destination stays lit while any hub section is on screen", () => {
    // SETTINGS is one of the five destinations now, so it carries "you are
    // here" for every hub section — including ones only reachable from inside
    // the hub. The profile button is lit only for its own page.
    useEventStore.setState({ activeSection: "appshots" });
    renderSidebar();

    expect(screen.getByTestId("nav-primary-settings").className).toMatch(/jarvis-nav-active/);
    expect(screen.getByTestId("sidebar-profile-toggle").className).not.toMatch(/jarvis-nav-active/);
  });

  test("the SETTINGS destination opens the hub on its MODEL page", () => {
    renderSidebar();

    fireEvent.click(screen.getByTestId("nav-primary-settings"));

    expect(useEventStore.getState().activeSection).toBe("apikeys");
    expect(useSettingsPage.getState().page).toBe("model");
  });

  test("the profile button is calm on non-hub sections", () => {
    useEventStore.setState({ activeSection: "board" });
    renderSidebar();

    expect(screen.getByTestId("sidebar-profile-toggle").className).not.toMatch(
      /jarvis-nav-active/,
    );
  });

  test("opens no popup — the hub is the only destination", () => {
    renderSidebar();
    fireEvent.click(screen.getByTestId("sidebar-profile-toggle"));

    expect(screen.queryByTestId("sidebar-profile-panel")).toBeNull();
    expect(screen.queryByTestId("sidebar-brain-card")).toBeNull();
  });
});

describe("Sidebar assistant name header", () => {
  beforeEach(() => {
    useEventStore.setState({
      voiceState: "idle",
      transcription: "",
      transcriptionFinal: true,
      connected: true,
      voiceReady: true,
    });
  });

  afterEach(() => {
    cleanup();
  });

  test("renders the resolved assistant name (not a hardcoded 'Jarvis')", () => {
    // The header wordmark must follow the configured assistant name so a user
    // who renames the assistant (e.g. to "Ruben") never sees a stale "Jarvis".
    useEventStore.setState({ assistantName: "Ruben" });

    renderSidebar();

    // The name lives in the search bar's accessible name now.
    const bar = screen.getByTestId("sidebar-search");
    expect(bar.getAttribute("aria-label")).toContain("Ruben");
    expect(bar.getAttribute("aria-label")).not.toContain("Jarvis");
  });

  test("follows a live assistant-name change", () => {
    useEventStore.setState({ assistantName: "Nova" });
    renderSidebar();
    const bar = () => screen.getByTestId("sidebar-search").getAttribute("aria-label") ?? "";
    expect(bar()).toContain("Nova");

    act(() => {
      useEventStore.setState({ assistantName: "Athena" });
    });

    expect(bar()).toContain("Athena");
    expect(bar()).not.toContain("Nova");
  });

  test("the search bar is a field you type into, not a door to a window", () => {
    renderSidebar();
    const bar = screen.getByTestId("sidebar-search") as HTMLInputElement;
    fireEvent.change(bar, { target: { value: "agen" } });
    expect(bar.value).toBe("agen");
    // Typing here never opens the Spotlight window in the middle.
    expect(useQuickSwitcher.getState().open).toBe(false);
  });

  test("switching the quick switcher off brings the name row back", () => {
    useEventStore.setState({ assistantName: "Ruben" });
    useQuickSwitchSettings.setState({ enabled: false });
    renderSidebar();
    expect(screen.queryByTestId("sidebar-search")).toBeNull();
    expect(screen.getByText("Ruben")).toBeTruthy();
    useQuickSwitchSettings.setState({ enabled: true });
  });
});

describe("Sidebar tool catalog navigation", () => {
  test("does not show broad tool catalog screens in the sidebar or quick index", () => {
    renderSidebar();

    for (const id of ["armory", "plugins", "mcps", "skills", "marketplace"]) {
      expect(screen.queryByTestId(`nav-row-${id}`)).toBeNull();
    }
    fireEvent.click(screen.getByTestId("sidebar-advanced-toggle"));
    for (const id of ["armory", "plugins", "mcps", "skills", "marketplace"]) {
      expect(screen.queryByTestId(`nav-row-${id}`)).toBeNull();
    }
  });
});

describe("Sidebar voice-boot indicator", () => {
  beforeEach(() => {
    useEventStore.setState({
      voiceState: "idle",
      transcription: "",
      transcriptionFinal: true,
      connected: true,
      voiceReady: false,
    });
  });

  afterEach(() => {
    cleanup();
  });

  test("shows a 'Voice starting…' spinner while connected but voice not ready", () => {
    // The window connects in ~1s but the voice feature warms up ~20s in the
    // background. During that gap the header must signal "starting", not the
    // normal idle "Ready" state (which would imply the mic already works).
    useEventStore.setState({ connected: true, voiceReady: false });

    const { container } = renderSidebar();

    expect(screen.getByText("Voice starting…")).toBeTruthy();
    expect(container.querySelector('[data-testid="voice-starting-spinner"]')).not.toBeNull();
    // The normal idle voice label must NOT be shown during warmup.
    expect(screen.queryByText("Ready")).toBeNull();
  });

  test("reverts to the normal voice state once voice is ready", () => {
    useEventStore.setState({ connected: true, voiceReady: true, voiceState: "idle" });

    const { container } = renderSidebar();

    // At rest the dot alone says "fine"; the word lives in its accessible
    // name and hover, not as a second line of visible text.
    expect(screen.getByRole("img", { name: "Ready" })).toBeTruthy();
    expect(screen.queryByText("Ready")).toBeNull();
    expect(screen.queryByText("Voice starting…")).toBeNull();
    expect(container.querySelector('[data-testid="voice-starting-spinner"]')).toBeNull();
  });

  test("shows 'Offline' (not the spinner) when disconnected and NOT warming", () => {
    // Truly offline: no live socket AND the WS is not in the fast-boot warming
    // loop (no 1013) — the honest state is Offline.
    useEventStore.setState({ connected: false, voiceReady: false, wsWarming: false });

    const { container } = renderSidebar();

    expect(screen.getByText("Offline")).toBeTruthy();
    expect(screen.queryByText("Voice starting…")).toBeNull();
    expect(container.querySelector('[data-testid="voice-starting-spinner"]')).toBeNull();
  });

  test("shows the booting label + spinner (not Offline) while warming", () => {
    // Disconnected but the fast-boot bootstrap keeps closing the WS with 1013:
    // the backend is still starting, so the honest state is "Starting…", not
    // the alarming "Offline".
    useEventStore.setState({ connected: false, voiceReady: false, wsWarming: true });

    const { container } = renderSidebar();

    expect(screen.getByText("Starting…")).toBeTruthy();
    expect(screen.queryByText("Offline")).toBeNull();
    expect(container.querySelector('[data-testid="voice-starting-spinner"]')).not.toBeNull();
  });
});

/*
 * The icon rail — what the sidebar becomes when it is dragged in.
 *
 * The seam used to stop at 200 px, which is still wide enough to read every
 * label; in the Agentic IDE that meant a fifth of the window stayed spent on a
 * nav list nobody was reading while a dozen terminals fought over the rest. Two
 * things have to hold for the rail to be a sidebar rather than a broken one:
 * every destination is still REACHABLE, and every icon still SAYS what it is.
 * A refactor that quietly drops either turns the rail into a column of mystery
 * glyphs, and nothing else on screen would look wrong.
 *
 * Anchored on the row's test id rather than its text: the label is translated,
 * so asserting on it would make these pass or fail with the active locale.
 */
describe("Sidebar icon rail", () => {
  beforeEach(() => {
    useEventStore.setState({
      voiceState: "idle",
      transcription: "",
      transcriptionFinal: true,
      connected: true,
      activeSection: "chats",
    });
  });

  afterEach(() => cleanup());

  test("shows its labels at the designed width", () => {
    renderSidebar(SIDEBAR_DEFAULT_WIDTH);

    expect(screen.getByTestId("sidebar").dataset.railed).toBe("false");
    fireEvent.click(screen.getByTestId("sidebar-advanced-toggle"));
    // The label is ON the row, and names the workspace rather than carrying
    // the retired generic "Chat" label shown in the product screenshot.
    expect(screen.getByTestId("nav-row-agentic-ide").textContent).toContain(
      "Agentic IDE",
    );
  });

  test("drops to icons once dragged past the snap point", () => {
    renderSidebar(SIDEBAR_RAIL_AT_WIDTH - 1);

    const aside = screen.getByTestId("sidebar");
    expect(aside.dataset.railed).toBe("true");
    // Snapped, not clipped: the band between the rail and a readable sidebar
    // shows half a word per row and reads as a rendering fault, so it is
    // skipped rather than rendered at the dragged width.
    expect(aside.style.width).toBe(`${SIDEBAR_RAIL_WIDTH}px`);
    expect(screen.getByTestId("nav-primary-agents").getAttribute("aria-label")).toBeTruthy();
  });

  test("keeps every destination named once its label is off the screen", () => {
    renderSidebar(SIDEBAR_RAIL_WIDTH);

    const row = screen.getByTestId("nav-primary-agents");
    expect(row.getAttribute("aria-label")).toBeTruthy();
    expect(row.getAttribute("title")).toBe(row.getAttribute("aria-label"));
  });

  test("still switches section on a click", () => {
    renderSidebar(SIDEBAR_RAIL_WIDTH);

    act(() => {
      screen.getByTestId("nav-primary-agents").click();
    });

    expect(useEventStore.getState().activeSection).toBe("agents");
  });

  test("keeps the rail canvas transparent and the active control glassy", () => {
    renderSidebar(SIDEBAR_RAIL_WIDTH);

    const sidebar = screen.getByTestId("sidebar");
    expect(sidebar.querySelector(".jarvis-shell-surface")).toBeNull();
    expect(screen.getByTestId("sidebar-new-chat")).toBeTruthy();
  });

  test("keeps the wake-word hint and realtime control off the rail", () => {
    // Both are label-shaped controls that cannot say anything useful in 64 px.
    // They step aside rather than being clipped into unreadable stubs.
    useEventStore.setState({
      voiceState: "listening",
      transcription: "auflegen",
      transcriptionFinal: false,
    });

    renderSidebar(SIDEBAR_RAIL_WIDTH);

    expect(screen.queryByText("auflegen")).toBeNull();
    // …and the navigation, which is the reason the rail exists, is still there.
    expect(screen.getByTestId("sidebar-new-chat")).toBeTruthy();
  });
});

/**
 * The collapse state (the toggle itself moved to the window caption).
 *
 * The sidebar header no longer carries its own button — it lives in the
 * caption's leading navigation (SectionNavButtons, owned by TopBar), beside
 * back/forward, so the controls sit in the empty caption corner on every
 * section. The collapsed STATE still belongs to the sidebar: the rail wins
 * while it holds, and the drag stays as the finer way in.
 */
describe("Sidebar collapse state", () => {
  beforeEach(() => {
    useEventStore.setState({ activeSection: "chats", connected: true });
  });

  afterEach(() => cleanup());

  function renderCollapsed(collapsed: boolean) {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    return render(
      <QueryClientProvider client={client}>
        <Sidebar
          width={SIDEBAR_DEFAULT_WIDTH}
          collapsed={collapsed}
        />
      </QueryClientProvider>,
    );
  }

  test("collapses to the rail even at a wide dragged width", () => {
    renderCollapsed(true);

    const aside = screen.getByTestId("sidebar");
    // The collapse is a STATE, not a width: the dragged 280 px is remembered
    // for the expand, and the rail wins while collapsed.
    expect(aside.dataset.railed).toBe("true");
    expect(aside.style.width).toBe(`${SIDEBAR_RAIL_WIDTH}px`);
  });

  test("expands to the dragged width once the state lifts", () => {
    const { rerender } = renderCollapsed(true);
    expect(screen.getByTestId("sidebar").dataset.railed).toBe("true");

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    rerender(
      <QueryClientProvider client={client}>
        <Sidebar
          width={SIDEBAR_DEFAULT_WIDTH}
          collapsed={false}
        />
      </QueryClientProvider>,
    );

    expect(screen.getByTestId("sidebar").dataset.railed).toBe("false");
  });

  test("carries no toggle of its own — the caption owns it", () => {
    // The header button moved to the caption's leading navigation
    // (SectionNavButtons): a sidebar that flipped its own state would drift
    // from the shell that owns the width beside it.
    renderCollapsed(false);

    expect(screen.queryByTestId("sidebar-collapse-toggle")).toBeNull();
    expect(screen.getByTestId("sidebar")).toBeTruthy();
  });
});

describe("compact sidebar navigation", () => {
  beforeEach(() => {
    useEventStore.setState({ activeSection: "chats", connected: true });
    useIdeChatStore.setState({ workspaces: [], view: "chat" });
  });
  afterEach(() => cleanup());

  test("shows exactly four destinations above the recent chats, the HUD first", () => {
    renderSidebar();
    const primary = screen.getByTestId("sidebar-primary-nav");
    const rows = Array.from(primary.querySelectorAll("[data-testid^='nav-primary-']"));
    // Chat, HUD and memory orb are ONE place (the HUD), so one row.
    expect(rows.map((row) => row.getAttribute("data-testid"))).toEqual([
      "nav-primary-chat",
      "nav-primary-agents",
      "nav-primary-work",
      "nav-primary-settings",
    ]);
    expect(screen.getByTestId("nav-primary-chat").textContent).toContain("HUD");
    // A new chat is the HUD row's own "+", not another row.
    expect(primary.querySelector("[data-testid='sidebar-new-chat']")).toBeTruthy();
    expect(screen.getByTestId("recent-chats")).toBeTruthy();
    // Advanced is folded by default.
    expect(screen.getByTestId("sidebar-advanced-toggle").getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("sidebar-advanced")).toBeNull();
    expect(screen.queryByTestId("nav-row-memory")).toBeNull();
  });

  test("each destination lands on its section", () => {
    renderSidebar();
    const lands: Record<string, string> = {
      chat: "chats",
      agents: "agents",
      work: "visualization",
      settings: "apikeys",
    };
    for (const [key, section] of Object.entries(lands)) {
      fireEvent.click(screen.getByTestId(`nav-primary-${key}`));
      expect(useEventStore.getState().activeSection).toBe(section);
    }
  });

  test("WORK stays lit on every work view", () => {
    // The coding workspace (WORK › Projects) swaps this column for its own
    // project navigation, so it is covered by the IDE tests above instead.
    for (const section of ["visualization", "board"] as const) {
      useEventStore.setState({ activeSection: section });
      renderSidebar();
      expect(screen.getByTestId("nav-primary-work").className, section).toMatch(/jarvis-nav-active/);
      cleanup();
    }
  });

  test("the HUD row stays lit for the old chat, HUD and memory-orb ids", () => {
    for (const section of ["chats", "deck", "orb"] as const) {
      useEventStore.setState({ activeSection: section });
      renderSidebar();
      expect(screen.getByTestId("nav-primary-chat").className, section).toMatch(/jarvis-nav-active/);
      expect(screen.getByTestId("nav-primary-work").className, section).not.toMatch(/jarvis-nav-active/);
      cleanup();
    }
  });

  test("expands Advanced without duplicating rows", () => {
    renderSidebar();
    fireEvent.click(screen.getByTestId("sidebar-advanced-toggle"));
    expect(screen.getByTestId("nav-row-memory")).toBeTruthy();
    expect(screen.getAllByTestId("nav-row-dictation")).toHaveLength(1);
    fireEvent.click(screen.getByTestId("nav-row-dictation"));
    expect(useEventStore.getState().activeSection).toBe("dictation");
    // The wiki notes are listed under Advanced only (the ORB destination is
    // part of the HUD now); away from them, Advanced folds again.
    fireEvent.click(screen.getByTestId("nav-primary-chat"));
    fireEvent.click(screen.getByTestId("sidebar-advanced-toggle"));
    expect(screen.queryByTestId("nav-row-memory")).toBeNull();
    expect(screen.getByTestId("recent-chats")).toBeTruthy();
  });

  test("Advanced opens by itself on a section only it lists", () => {
    useEventStore.setState({ activeSection: "dictation" });
    renderSidebar();
    expect(screen.getByTestId("sidebar-advanced")).toBeTruthy();
    expect(screen.getByTestId("nav-row-dictation").className).toMatch(/jarvis-nav-active/);
  });

  test("the profile button opens the hub — hub tabs are the hub's own tests", () => {
    // No popup anymore: entries like Spend live in the hub's left nav
    // (see SettingsHubView.test), so one click on the profile button is the
    // whole interaction and it lands on the Profile tab.
    renderSidebar();
    fireEvent.click(screen.getByTestId("sidebar-profile-toggle"));
    expect(useEventStore.getState().activeSection).toBe("profile");
    expect(screen.queryByTestId("sidebar-profile-panel")).toBeNull();
  });

  test("every registered destination remains reachable", () => {
    renderSidebar();
    fireEvent.click(screen.getByTestId("sidebar-advanced-toggle"));
    const primaryRow: Record<string, string> = { chats: "chat", agents: "agents" };
    for (const item of [...NAV_GROUPS.flat(), ...NAV_FOOTER_ITEMS]) {
      // Hub entries are one SETTINGS click away (covered above); the hub
      // selects their tab itself (see SettingsHubView.test).
      if ((SETTINGS_HUB_IDS as readonly string[]).includes(item.id)) continue;
      const testId = item.id in primaryRow ? `nav-primary-${primaryRow[item.id]}` : `nav-row-${item.id}`;
      expect(screen.getByTestId(testId), item.id).toBeTruthy();
    }
    // And every hub id is still a valid section id — an id the hub lists
    // that stops resolving strands deep links, the deck and voice commands.
    for (const id of SETTINGS_HUB_IDS) {
      expect(isSectionId(id)).toBe(true);
    }
  });
});
