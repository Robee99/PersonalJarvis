import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GhostView, tally } from "@/views/GhostView";
import { LiveView } from "@/views/LiveView";
import { reportFrame, resetFrameReports, DEAD_AFTER } from "@/components/stark/CctvWall";
import { useI18nStore } from "@/i18n";
import type { CctvCamera, CctvCatalog } from "@/lib/cctv";
import type { LiveCatalog, LiveStream } from "@/lib/live";
import { useEventStore } from "@/store/events";
import { useIntelStore } from "@/store/intel";

/**
 * The Ghost screen's camera wall against a served catalog: it fills with
 * only the tiles on screen, narrows to a country or a city at once, finds
 * cameras by words, opens one large with keyboard stepping, plays a YouTube
 * camera only when opened, and its counts open their own views. The Live
 * screen keeps only the news. `fetch` is a plain fake.
 */

function cam(id: string, city: string, country: string, code: string, extra: Partial<CctvCamera> = {}): CctvCamera {
  return { id, name: `${city} cam ${id}`, city, city_id: city.toLowerCase(), country, country_code: code, provider: `${city} DOT`, lat: 30, lon: -97, feed: "image", heading: 90, ...extra };
}

const AUSTIN = Array.from({ length: 300 }, (_, i) => cam(`a${i}`, "Austin", "United States", "US"));
const CATALOG: CctvCatalog = {
  available: true,
  reason: null,
  gev: "http://127.0.0.1:4173",
  cameras: [
    ...AUSTIN,
    cam("h1", "Helsinki", "Finland", "FI", { lat: 60.17, lon: 24.94, provider: "Fintraffic" }),
    cam("h2", "Helsinki", "Finland", "FI", { lat: 60.17, lon: 24.94, provider: "Fintraffic" }),
    cam("t1", "Tampere", "Finland", "FI", { lat: 61.5, lon: 23.8, provider: "Fintraffic" }),
    cam("l1", "London", "United Kingdom", "GB", { lat: 51.5, lon: -0.1, provider: "Transport for London" }),
    cam("v1", "London", "United Kingdom", "GB", { feed: "video" }),
  ],
  cities: [],
  fetched_at: 0,
};

const TOKYO: LiveStream = { key: "tokyo-shinjuku", name: "Tokyo", short: "Tokyo", kind: "camera", group: "asia", place: "Shinjuku skyline", embed: "abcdefghijk", page: "https://www.youtube.com/watch?v=abcdefghijk" };
const BBC: LiveStream = { ...TOKYO, key: "bbc", name: "BBC News", short: "BBC", kind: "news", group: "world", place: "" };
const LIVE: LiveCatalog = { streams: [BBC, TOKYO], groups: [{ id: "asia", name: "Asia" }], news_groups: [{ id: "world", name: "World" }] };

function serve(catalog: CctvCatalog) {
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    const url = String(input);
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
    if (url === "/api/intel/cctv") return json(catalog);
    if (url === "/api/intel/cctv/health") return json({ ok: 4, degraded: 1, error: 0, unknown: 0 });
    if (url === "/api/intel/live") return json(LIVE);
    if (url.startsWith("/api/intel/live/now/")) return json({ video: null });
    return new Response("{}", { status: 404 });
  });
}

function withClient(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

const tiles = () => screen.queryAllByTestId("stark-ghost-tile");
const frames = () => screen.queryAllByTestId("stark-cctv-frame") as HTMLImageElement[];
const wall = () => screen.getByTestId("stark-ghost-wall");

beforeEach(() => {
  useI18nStore.getState().setUi("en", { push: false });
  useEventStore.setState({ connected: true, voiceState: "idle", assistantName: "Jarvis", activeSection: "ghost" });
  useIntelStore.setState({ watch: null });
  window.localStorage.clear();
  resetFrameReports();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("the Ghost camera wall", () => {
  it("fills the screen with cameras but draws only the tiles in view, and has no text strip", async () => {
    serve(CATALOG);
    withClient(<GhostView />);
    await waitFor(() => expect(Number(wall().dataset.count)).toBe(305));
    expect(tiles().length).toBeGreaterThan(10);
    expect(tiles().length).toBeLessThan(60);
    expect(frames().length).toBeLessThanOrEqual(tiles().length);
    expect(frames()[0].src).toMatch(/^http:\/\/127\.0\.0\.1:4173\/api\/cctv\/frame\/a0\?t=\d+$/);
    // Countries take turns at the top of the wall; GEV's video-only camera stays off it.
    expect(tiles().slice(0, 4).map((t) => t.dataset.camera)).toEqual(["a0", "h1", "yt:tokyo-shinjuku", "l1"]);
    expect(screen.queryByTestId("stark-ghost-strip")).toBeNull();
    expect(screen.queryByTestId("stark-ghost-drawer")).toBeNull();
  });

  it("narrows to a country, then a city, from the picker", async () => {
    serve(CATALOG);
    withClient(<GhostView />);
    await waitFor(() => expect(Number(wall().dataset.count)).toBe(305));
    fireEvent.click(screen.getByTestId("stark-ghost-place"));
    const picker = screen.getByTestId("stark-ghost-picker");
    fireEvent.click(within(picker).getByTestId("stark-ghost-country-fi"));
    expect(wall().dataset.count).toBe("3");
    fireEvent.click(within(picker).getByTestId("stark-ghost-city-fi/tampere"));
    expect(screen.queryByTestId("stark-ghost-picker")).toBeNull();
    expect(tiles().map((t) => t.dataset.camera)).toEqual(["t1"]);
    expect(screen.getByTestId("stark-ghost-place").textContent).toContain("Tampere");
    // Next city stays in Finland; the clear button goes back to the world.
    fireEvent.click(screen.getByTestId("stark-ghost-next-city"));
    expect(tiles().map((t) => t.dataset.camera)).toEqual(["h1", "h2"]);
    fireEvent.click(screen.getByTestId("stark-ghost-clear-place"));
    expect(wall().dataset.count).toBe("305");
  });

  it("searches cities in the picker and cameras from the toolbar", async () => {
    serve(CATALOG);
    withClient(<GhostView />);
    await waitFor(() => expect(Number(wall().dataset.count)).toBe(305));
    fireEvent.click(screen.getByTestId("stark-ghost-place"));
    fireEvent.change(screen.getByTestId("stark-ghost-picker-search"), { target: { value: "lond" } });
    fireEvent.keyDown(screen.getByTestId("stark-ghost-picker-search"), { key: "Enter" });
    expect(tiles().map((t) => t.dataset.camera)).toEqual(["l1"]);
    fireEvent.change(screen.getByTestId("stark-ghost-search"), { target: { value: "fintraffic" } });
    expect(wall().dataset.count).toBe("3");
  });

  it("opens a camera large and steps with the arrow keys; Escape goes back", async () => {
    serve(CATALOG);
    withClient(<GhostView />);
    await waitFor(() => expect(Number(wall().dataset.count)).toBe(305));
    fireEvent.click(screen.getByTestId("stark-ghost-search"));
    fireEvent.change(screen.getByTestId("stark-ghost-search"), { target: { value: "helsinki" } });
    fireEvent.click(screen.getByRole("button", { name: /Show Helsinki cam h1 large/ }));
    const focus = screen.getByTestId("stark-ghost-focus");
    expect(focus.dataset.camera).toBe("h1");
    expect(focus.textContent).toContain("Fintraffic");
    expect(focus.textContent).toContain("Finland");
    expect(screen.getByTestId("stark-ghost-focus-index").textContent).toContain("1 / 2");
    expect(screen.getByTestId("stark-ghost-pin")).toBeTruthy();
    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    expect(screen.getByTestId("stark-ghost-focus").dataset.camera).toBe("h2");
    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    expect(screen.getByTestId("stark-ghost-focus").dataset.camera).toBe("h1");
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByTestId("stark-ghost-focus")).toBeNull();
  });

  it("plays a YouTube city camera only once it is opened", async () => {
    serve(CATALOG);
    withClient(<GhostView />);
    await waitFor(() => expect(screen.getAllByTestId("stark-ghost-video-tile")).toHaveLength(1));
    expect(screen.queryByTestId("stark-live-frame")).toBeNull();
    fireEvent.click(screen.getByTestId("stark-ghost-video-tile"));
    const player = await screen.findByTestId("stark-live-frame");
    expect(player.getAttribute("src")).toContain("mute=1");
    expect(player.getAttribute("src")).toContain("autoplay=1");
    expect(screen.getByTestId("stark-ghost-focus").textContent).toContain("Japan");
  });

  it("opens a camera the assistant asked for", async () => {
    serve(CATALOG);
    withClient(<GhostView />);
    await waitFor(() => expect(Number(wall().dataset.count)).toBe(305));
    act(() => useIntelStore.getState().applyCommand({ action: "watch", stream: "tokyo-shinjuku" }));
    expect(screen.getByTestId("stark-ghost-focus").dataset.camera).toBe("yt:tokyo-shinjuku");
    expect(screen.getByTestId("stark-ghost-place").textContent).toContain("Tokyo");
  });

  it("each count opens its own view", async () => {
    serve(CATALOG);
    withClient(<GhostView />);
    await waitFor(() => expect(Number(wall().dataset.count)).toBe(305));
    fireEvent.click(screen.getByTestId("stark-ghost-stat-cities"));
    expect(screen.getByTestId("stark-ghost-picker")).toBeTruthy();
    fireEvent.click(screen.getByTestId("stark-ghost-stat-cities"));
    fireEvent.click(screen.getByTestId("stark-ghost-stat-frames"));
    expect(screen.getByTestId("stark-ghost-panel-health").textContent).toContain("God’s Eye View");
    fireEvent.click(screen.getByTestId("stark-ghost-tab-operators"));
    fireEvent.click(within(screen.getByTestId("stark-ghost-panel-operators")).getByText("Fintraffic"));
    expect(wall().dataset.count).toBe("3");
    fireEvent.click(screen.getByTestId("stark-ghost-stat-stations"));
    expect(screen.getByTestId("stark-ghost-map")).toBeTruthy();
    expect(screen.getByTestId("stark-ghost-layer-stations").getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByTestId("stark-ghost-layer-cameras"));
    fireEvent.click(screen.getByRole("button", { name: /Helsinki · 2 cameras/ }));
    expect(wall().dataset.count).toBe("2");
    fireEvent.click(screen.getByTestId("stark-ghost-stat-cameras"));
    expect(wall().dataset.count).toBe("305");
  });

  it("changes the tile size and remembers it", async () => {
    serve(CATALOG);
    withClient(<GhostView />);
    await waitFor(() => expect(Number(wall().dataset.count)).toBe(305));
    const medium = Number(wall().dataset.cols);
    fireEvent.click(screen.getByTestId("stark-ghost-size-s"));
    expect(Number(wall().dataset.cols)).toBeGreaterThan(medium);
    fireEvent.click(screen.getByTestId("stark-ghost-size-l"));
    expect(Number(wall().dataset.cols)).toBeLessThan(medium);
    expect(JSON.parse(window.localStorage.getItem("ghost.wall.v1") ?? "{}").size).toBe("l");
  });

  it("drops a city from the picker once its cameras keep showing no picture", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve(CATALOG);
    withClient(<GhostView />);
    await waitFor(() => expect(Number(wall().dataset.count)).toBe(305));
    for (let i = 0; i < DEAD_AFTER; i++) reportFrame("t1", false);
    act(() => {
      vi.advanceTimersByTime(1100);
    });
    fireEvent.click(screen.getByTestId("stark-ghost-place"));
    fireEvent.click(screen.getByTestId("stark-ghost-country-fi"));
    expect(screen.queryByTestId("stark-ghost-city-fi/tampere")).toBeNull();
    expect(screen.getByTestId("stark-ghost-city-fi/helsinki")).toBeTruthy();
  });

  it("still shows the YouTube cameras when God's Eye View is not running", async () => {
    serve({ ...CATALOG, available: false, reason: "gev_down", cameras: [], cities: [] });
    withClient(<GhostView />);
    await waitFor(() => expect(screen.getByTestId("stark-ghost-gev").textContent).toContain("God's Eye View is not running"));
    await waitFor(() => expect(wall().dataset.count).toBe("1"));
  });

  it("counts cameras by operator and region", () => {
    const { providers, regions } = tally(CATALOG.cameras.filter((c) => c.feed === "image"));
    expect(providers[0]).toEqual(["Austin DOT", 300]);
    expect(Object.fromEntries(regions)).toEqual({ north_america: 300, europe: 4 });
  });
});

describe("the Live screen", () => {
  it("shows the news only", async () => {
    serve(CATALOG);
    withClient(<LiveView />);
    await screen.findByTestId("stark-live-news");
    expect(screen.queryByTestId("stark-live-cameras")).toBeNull();
    expect(screen.queryByTestId("stark-cctv")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("stark-live-totals").textContent).not.toContain("camera"));
  });
});
