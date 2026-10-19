import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The Live and Ghost screens' camera parts against a stubbed
 * `/api/intel/cctv`: the wall by city, the search, the large view, the
 * God's-Eye-View-is-down message, and the Ghost helpers that pick featured
 * cameras and count them. The command bar and WebGL globe are stubbed.
 */
vi.mock("@/components/stark/CommandBar", () => ({ CommandBar: () => <div data-testid="stark-command-bar" /> }));
vi.mock("@/components/stark/OrbitGlobe", () => ({ OrbitGlobe: () => <div data-testid="stark-orbit-globe" /> }));

import { CctvWall, wallCameras } from "@/components/stark/CctvWall";
import { GhostView, featuredCameras, tally } from "@/views/GhostView";
import { useI18nStore } from "@/i18n";
import type { CctvCamera, CctvCatalog } from "@/lib/cctv";
import { useEventStore } from "@/store/events";

function cam(id: string, city: string, extra: Partial<CctvCamera> = {}): CctvCamera {
  return { id, name: `${city} cam ${id}`, city, city_id: city.toLowerCase(), provider: `${city} DOT`, lat: 30, lon: -97, feed: "image", heading: 90, ...extra };
}

const AUSTIN = Array.from({ length: 11 }, (_, i) => cam(`a${i}`, "Austin"));
const CATALOG: CctvCatalog = {
  available: true,
  reason: null,
  gev: "http://127.0.0.1:4173",
  cameras: [...AUSTIN, cam("l1", "London", { lat: 51.5, lon: -0.1, provider: "Transport for London" }), cam("v1", "London", { feed: "video" })],
  cities: [
    { id: "austin", name: "Austin", count: 11, video: 0, lat: 30, lon: -97, providers: ["Austin DOT"] },
    { id: "london", name: "London", count: 2, video: 1, lat: 51.5, lon: -0.1, providers: ["Transport for London"] },
  ],
  fetched_at: 0,
};

function serve(catalog: CctvCatalog) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/intel/cctv") return new Response(JSON.stringify(catalog), { status: 200 });
      if (url === "/api/intel/cctv/health") return new Response(JSON.stringify({ ok: 4, degraded: 1, error: 0, unknown: 0 }), { status: 200 });
      return new Response("{}", { status: 404 });
    }),
  );
}

function withClient(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

const frames = () => screen.queryAllByTestId("stark-cctv-frame") as HTMLImageElement[];

beforeEach(() => {
  useI18nStore.getState().setUi("en", { push: false });
  useEventStore.setState({ connected: true, voiceState: "idle", assistantName: "Jarvis", activeSection: "live-feeds" });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the camera wall", () => {
  it("shows nine frames of the largest city, straight from God's Eye View, and pages on", async () => {
    serve(CATALOG);
    withClient(<CctvWall />);
    await waitFor(() => expect(frames()).toHaveLength(9));
    expect(frames()[0].src).toMatch(/^http:\/\/127\.0\.0\.1:4173\/api\/cctv\/frame\/a0\?t=\d+$/);
    fireEvent.click(screen.getByTestId("stark-cctv-next"));
    expect(frames().map((f) => f.dataset.camera)).toEqual(["a9", "a10"]);
  });

  it("finds cameras by words and opens one large", async () => {
    serve(CATALOG);
    withClient(<CctvWall />);
    await waitFor(() => expect(frames()).toHaveLength(9));
    fireEvent.change(screen.getByTestId("stark-cctv-search"), { target: { value: "transport for london" } });
    expect(frames().map((f) => f.dataset.camera)).toEqual(["l1"]);
    fireEvent.click(screen.getByRole("button", { name: /Show London cam l1 large/ }));
    expect(screen.getByTestId("stark-cctv-focus").textContent).toContain("Transport for London");
    fireEvent.click(screen.getByTestId("stark-cctv-back"));
    expect(screen.queryByTestId("stark-cctv-focus")).toBeNull();
  });

  it("says when God's Eye View is not running", async () => {
    serve({ ...CATALOG, available: false, reason: "gev_down", cameras: [], cities: [] });
    withClient(<CctvWall />);
    expect((await screen.findByTestId("stark-cctv-down")).textContent).toContain("God's Eye View is not running");
  });

  it("leaves video cameras off the wall", () => {
    expect(wallCameras(CATALOG, "london", "").map((c) => c.id)).toEqual(["l1"]);
  });
});

describe("the Ghost screen", () => {
  it("features one camera from each of the largest cities, turning through them", () => {
    expect(featuredCameras(CATALOG, 3, 0).map((c) => c.id)).toEqual(["a0", "l1"]);
    expect(featuredCameras(CATALOG, 3, 12).map((c) => c.id)).toEqual(["a1", "l1"]);
    expect(featuredCameras(undefined, 3, 0)).toEqual([]);
  });

  it("counts cameras by operator and region", () => {
    const { providers, regions } = tally(CATALOG.cameras.filter((c) => c.feed === "image"));
    expect(providers[0]).toEqual(["Austin DOT", 11]);
    expect(Object.fromEntries(regions)).toEqual({ north_america: 11, europe: 1 });
  });

  it("puts camera cities on the fusion map and opens a city's cameras in focus", async () => {
    serve(CATALOG);
    withClient(<GhostView />);
    await waitFor(() => expect(screen.getByTestId("stark-ghost-stats").textContent).toContain("2"));
    const austin = await screen.findByRole("button", { name: /Austin · 11 cameras/ });
    fireEvent.click(austin);
    const focus = await screen.findByTestId("stark-ghost-focus-camera");
    expect(focus.textContent).toContain("Austin DOT");
    fireEvent.click(screen.getByTestId("stark-ghost-next"));
    expect(screen.getByTestId("stark-ghost-focus").textContent).toContain("2/11");
    fireEvent.click(screen.getByTestId("stark-ghost-layer-stations"));
    expect(screen.queryByRole("button", { name: /Austin · 11 cameras/ })).toBeNull();
  });
});
