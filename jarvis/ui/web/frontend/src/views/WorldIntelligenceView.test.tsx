import { fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_GEV_EMBED_URL,
  GEV_EMBED_URL,
  GEV_READY_TIMEOUT_MS,
  resolveGeVEmbedUrl,
  WorldIntelligenceView,
} from "@/views/WorldIntelligenceView";
import { ago } from "@/components/stark/WorldPanels";
import { useI18nStore } from "@/i18n";
import { useIntelStore } from "@/store/intel";
import { DEFAULT_FILTERS } from "@/lib/intel";

const NEWS = {
  items: [
    { title: "Newest headline", link: "https://example.org/a", source: "BBC World", published: 1_700_000_000 },
    { title: "Older headline", link: "https://example.org/b", source: "NPR World", published: 1_699_990_000 },
  ],
  sources: ["BBC World", "Al Jazeera", "NPR World"],
  failed: ["Al Jazeera"],
  fetched_at: 1_700_000_100,
};

const META = { available: true, stale: false, fetched_at: 1_700_000_000, source: "Test", source_url: "https://example.org" };
const PUBLIC: Record<string, unknown> = {
  "/api/public/quakes": {
    ...META,
    items: [{ id: "a", mag: 6.4, place: "off Japan", time_ms: Date.now() - 60_000, lon: 142, lat: 38, depth_km: 20, url: null, alert: null, tsunami: false, sig: 600 }],
  },
  "/api/public/events": {
    ...META,
    items: [{ id: "v", title: "Etna eruption", category: "Volcanoes", category_id: "volcanoes", lon: 15, lat: 37.7, date_ms: Date.now() - 7_200_000, link: null, magnitude: null, unit: null }],
  },
  "/api/public/iss": { ...META, lat: 10, lon: 60, alt_km: 420, vel_kmh: 27_600, time_ms: Date.now() },
  "/api/public/stations": { ...META, items: [] },
};

const BASE_EVENT = {
  summary: null,
  automated: false,
  updated_at: null,
  place: null,
  country: null,
  severity: null,
  severity_label: null,
  attrs: {},
  ingested_at: null,
  related: [],
  stale: false,
};
const INTEL = {
  rev: 3,
  unchanged: false,
  events: [
    {
      ...BASE_EVENT,
      id: "usgs:us6000u3bc",
      source: "usgs",
      kind: "earthquake",
      category: "hazard",
      title: "M 5.9 earthquake — 185 km NW of Port-Olry, Vanuatu",
      evidence: "measured",
      occurred_at: Date.now() - 3_600_000,
      lat: -14.0261,
      lon: 165.7054,
      severity: 2,
      severity_label: "M 5.9",
      links: [{ name: "USGS event page", url: "https://earthquake.usgs.gov/earthquakes/eventpage/us6000u3bc" }],
      related: ["gdacs:EQ-1572508"],
    },
    {
      ...BASE_EVENT,
      id: "gdacs:EQ-1572508",
      source: "gdacs",
      kind: "earthquake",
      category: "hazard",
      title: "Earthquake in Vanuatu",
      evidence: "agency",
      automated: true,
      occurred_at: Date.now() - 3_000_000,
      lat: -14.1387,
      lon: 165.6383,
      severity: 1,
      severity_label: "GDACS green alert",
      links: [{ name: "GDACS report", url: "javascript:alert(1)" }],
    },
    {
      ...BASE_EVENT,
      id: "gdelt:protest--2106102-20261019",
      source: "gdelt",
      kind: "protest",
      category: "unrest",
      title: "Protest reported in New Delhi, Delhi, India",
      summary: "Machine-coded from 1 news article (1 outlet) by the GDELT Project. A media report, not a confirmed event.",
      evidence: "media",
      automated: true,
      occurred_at: Date.now() - 1_800_000,
      lat: 28.6,
      lon: 77.2,
      place: "New Delhi, Delhi, India",
      links: [{ name: "organiser.org", url: "https://organiser.org/2026/10/19/417506/" }],
    },
    {
      ...BASE_EVENT,
      id: "usgs:nowhere",
      source: "usgs",
      kind: "earthquake",
      category: "hazard",
      title: "M 4.6 earthquake",
      evidence: "measured",
      occurred_at: Date.now() - 600_000,
      lat: null,
      lon: null,
      links: [],
    },
  ],
  sources: [
    { id: "usgs", name: "USGS Earthquake Hazards Program", attribution: "USGS", home_url: "https://earthquake.usgs.gov/", terms_url: "https://www.usgs.gov/information-policies-and-instructions", status: "ok", fetched_at: Date.now(), count: 2, error: null, refresh_s: 300 },
    { id: "gdelt", name: "GDELT Project", attribution: "GDELT", home_url: "https://www.gdeltproject.org/", terms_url: "https://www.gdeltproject.org/about.html#termsofuse", status: "stale", fetched_at: Date.now() - 3_600_000, count: 1, error: "down", refresh_s: 900 },
  ],
  regions: [{ id: "south-asia", name: "South Asia", boxes: [[5, 60, 37.5, 97.5]] }],
};

function render(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

describe("WorldIntelligenceView", () => {
  beforeEach(() => {
    useI18nStore.getState().setUi("en", { push: false });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/deck/news") return new Response(JSON.stringify(NEWS), { status: 200 });
        if (url in PUBLIC) return new Response(JSON.stringify(PUBLIC[url]), { status: 200 });
        if (url.startsWith("/api/intel/events"))
          return new Response(JSON.stringify(url.includes("rev=3") ? { rev: 3, unchanged: true } : INTEL), { status: 200 });
        return new Response("{}", { status: 404 });
      }),
    );
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    useIntelStore.setState({ filters: DEFAULT_FILTERS, selectedId: null, selectedVia: null });
  });

  it("shows the news broadcast, the feeds that answered and the ticker", async () => {
    render(<WorldIntelligenceView />);
    fireEvent.click(screen.getByTestId("stark-world-tab-news"));
    const items = await screen.findAllByTestId("stark-news-item");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("Newest headline");
    expect(items[0].querySelector("a")?.getAttribute("href")).toBe("https://example.org/a");
    expect(items[0].querySelector("a")?.getAttribute("rel")).toContain("noopener");
    expect(screen.getAllByTestId("stark-feed-up")).toHaveLength(2);
    expect(screen.getByTestId("stark-feed-down").textContent).toContain("Al Jazeera");
    expect(screen.getByTestId("stark-ticker").textContent).toContain("Older headline");
  });

  it("says how old a story is", () => {
    const now = 1_700_000_000;
    expect(ago(null, now)).toBe("—");
    expect(ago(now - 30, now)).toBe("now");
    expect(ago(now - 12 * 60, now)).toBe("12m");
    expect(ago(now - 3 * 3600, now)).toBe("3h");
    expect(ago(now - 2 * 86_400, now)).toBe("2d");
  });

  it("embeds the local God's Eye View runtime with voice permission", () => {
    render(<WorldIntelligenceView />);

    const frame = screen.getByTitle("God’s Eye View") as HTMLIFrameElement;
    expect(frame.src).toBe(GEV_EMBED_URL);
    expect(new URL(frame.src).hostname).toBe("127.0.0.1");
    expect(new URL(frame.src).searchParams.get("embed")).toBe("1");
    expect(frame.getAttribute("allow")).toContain("microphone");
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(
      screen.getByRole("link", { name: /open separately/i }).getAttribute("href"),
    ).toBe(GEV_EMBED_URL);
  });

  it("allows configured loopback ports and rejects remote view servers", () => {
    expect(resolveGeVEmbedUrl()).toBe("http://127.0.0.1:4173/?embed=1");
    expect(resolveGeVEmbedUrl("http://localhost:4182/path?style=flir")).toBe(
      "http://localhost:4182/path?style=flir&embed=1",
    );
    expect(resolveGeVEmbedUrl("https://example.com/" )).toBeNull();
    expect(resolveGeVEmbedUrl("http://user:pass@127.0.0.1:4173/")).toBeNull();
    expect(DEFAULT_GEV_EMBED_URL).toBe("http://127.0.0.1:4173/");
  });

  it("waits for GEV readiness, forwards a requested view, and reports its acknowledgement", () => {
    render(<WorldIntelligenceView />);
    const frame = screen.getByTitle("God’s Eye View") as HTMLIFrameElement;
    const target = frame.contentWindow;
    if (!target) throw new Error("iframe has no content window");
    const postMessage = vi.spyOn(target, "postMessage");
    const origin = new URL(GEV_EMBED_URL!).origin;
    const view = { camera: { lat: 30, lon: -97, altitude_m: 1000 } };

    act(() => {
      window.dispatchEvent(new CustomEvent("jarvis:gev-view", { detail: { view } }));
    });
    expect(postMessage).not.toHaveBeenCalled();

    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: "gev:ready" },
          origin,
          source: target,
        }),
      );
    });
    expect(postMessage).toHaveBeenCalledTimes(1);
    expect(postMessage.mock.calls[0][0]).toMatchObject({ type: "gev:view", view });
    expect(postMessage.mock.calls[0][1]).toBe(origin);
    expect(screen.getByText(/Applying requested view/)).toBeTruthy();

    const request = postMessage.mock.calls[0][0] as { id: string };
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: "gev:view-applied", id: request.id, ok: true },
          origin,
          source: target,
        }),
      );
    });
    expect(screen.getByText(/View applied/)).toBeTruthy();
  });

  it("hands over to the built-in globe when GEV does not answer, with working layers and controls", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<WorldIntelligenceView />);
    expect(screen.getByTestId("stark-gev-waiting")).toBeTruthy();
    expect(screen.queryByTestId("stark-builtin-globe")).toBeNull();
    act(() => {
      vi.advanceTimersByTime(GEV_READY_TIMEOUT_MS + 10);
    });
    expect(screen.getByTestId("stark-gev-status").getAttribute("data-state")).toBe("down");
    expect(screen.getByTestId("stark-builtin-globe")).toBeTruthy();
    expect(screen.getByTestId("stark-gev-down-note")).toBeTruthy();
    // GEV stays mounted (hidden), so it can still connect.
    expect((screen.getByTitle("God’s Eye View") as HTMLIFrameElement).className).toContain("hidden");

    // Layers: the placed incidents (the one without a position stays off the
    // globe), the ISS, and a toggle that takes a layer's points off.
    await waitFor(() => expect(screen.getByTestId("stark-world-layer-incidents").textContent).toContain("3"));
    await waitFor(() => expect(screen.getByTestId("stark-world-layer-space").textContent).toContain("1"));
    fireEvent.click(screen.getByTestId("stark-world-layer-incidents"));
    expect(screen.getByTestId("stark-world-layer-incidents").getAttribute("aria-pressed")).toBe("false");

    // Controls: zoom both ways, pause the spin, back home.
    fireEvent.click(screen.getByTestId("stark-globe-zoom-in"));
    expect(screen.getByTestId("stark-globe-scale").textContent).toBe("1.2");
    fireEvent.click(screen.getByTestId("stark-globe-zoom-out"));
    fireEvent.click(screen.getByTestId("stark-globe-zoom-out"));
    expect(screen.getByTestId("stark-globe-scale").textContent).toBe("0.8");
    expect(screen.getByTestId("stark-globe-zoom-out").hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByTestId("stark-globe-spin"));
    expect(screen.getByTestId("stark-globe-spin").getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByTestId("stark-globe-reset"));
    expect(screen.getByTestId("stark-globe-scale").textContent).toBe("1");

    // Either globe can be picked by hand.
    fireEvent.click(screen.getByTestId("stark-globe-gev"));
    expect(screen.queryByTestId("stark-builtin-globe")).toBeNull();
    expect(screen.getByTestId("stark-gev-waiting").textContent).toContain("did not answer");
  });

  it("a requested view lands on the built-in globe when GEV is down", () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<WorldIntelligenceView />);
    act(() => {
      vi.advanceTimersByTime(GEV_READY_TIMEOUT_MS + 10);
    });
    const frame = screen.getByTitle("God’s Eye View") as HTMLIFrameElement;
    const postMessage = vi.spyOn(frame.contentWindow!, "postMessage");
    act(() => {
      window.dispatchEvent(new CustomEvent("jarvis:gev-view", { detail: { view: { camera: { lat: 35.7, lon: 139.7 } } } }));
    });
    expect(postMessage).not.toHaveBeenCalled();
    expect(screen.getByTestId("stark-builtin-globe")).toBeTruthy();
  });
});

describe("World incidents", () => {
  const origin = new URL(GEV_EMBED_URL!).origin;

  beforeEach(() => {
    useI18nStore.getState().setUi("en", { push: false });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url in PUBLIC) return new Response(JSON.stringify(PUBLIC[url]), { status: 200 });
        if (url.startsWith("/api/intel/events")) return new Response(JSON.stringify(INTEL), { status: 200 });
        return new Response("{}", { status: 404 });
      }),
    );
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    useIntelStore.setState({ filters: DEFAULT_FILTERS, selectedId: null, selectedVia: null });
  });

  function ready(capabilities?: string[]) {
    const frame = screen.getByTitle("God’s Eye View") as HTMLIFrameElement;
    const target = frame.contentWindow!;
    const postMessage = vi.spyOn(target, "postMessage");
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data: { type: "gev:ready", capabilities }, origin, source: target }));
    });
    return { target, postMessage };
  }

  it("lists incidents with what each rests on, and opens one with its sources", async () => {
    render(<WorldIntelligenceView />);
    const rows = await screen.findAllByTestId("incident-row");
    expect(rows).toHaveLength(4);
    // Most severe first; an incident without a position is still listed.
    expect(rows[0].textContent).toContain("M 5.9 earthquake");
    expect(rows.map((r) => r.querySelector("[data-testid=incident-evidence]")?.textContent)).toContain("Media");
    expect(screen.getByTestId("incident-sources").textContent).toContain("GDELT Project");
    expect(screen.getByTestId("incident-sources").querySelector("[data-state=stale]")).toBeTruthy();

    fireEvent.click(rows[0]);
    const detail = screen.getByTestId("incident-detail");
    expect(detail.getAttribute("data-incident-id")).toBe("usgs:us6000u3bc");
    const link = detail.querySelector("[data-testid=incident-links] a")!;
    expect(link.getAttribute("href")).toBe("https://earthquake.usgs.gov/earthquakes/eventpage/us6000u3bc");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    // The other feed's report of the same quake is one click away.
    fireEvent.click(screen.getByTestId("incident-related").querySelector("button")!);
    const gdacs = screen.getByTestId("incident-detail");
    expect(gdacs.getAttribute("data-incident-id")).toBe("gdacs:EQ-1572508");
    // A javascript: link from a feed is shown as text, never as a link.
    expect(gdacs.querySelector("[data-testid=incident-links] a")).toBeNull();
    expect(gdacs.querySelector("[data-testid=incident-links]")!.textContent).toContain("GDACS report");
  });

  it("never presents a media report as confirmed", async () => {
    render(<WorldIntelligenceView />);
    fireEvent.click(await screen.findByText("Protest reported in New Delhi, Delhi, India"));
    expect(screen.getByTestId("incident-unconfirmed").textContent).toMatch(/Unconfirmed media report/);
    expect(screen.getByTestId("incident-detail").textContent).toContain("Not rated");
  });

  it("filters by category, region and evidence", async () => {
    render(<WorldIntelligenceView />);
    await screen.findAllByTestId("incident-row");
    fireEvent.click(screen.getByTestId("incident-category-unrest"));
    expect(screen.getAllByTestId("incident-row")).toHaveLength(1);
    fireEvent.click(screen.getByTestId("incident-category-all"));
    fireEvent.change(screen.getByTestId("incident-region"), { target: { value: "south-asia" } });
    expect(screen.getAllByTestId("incident-row")).toHaveLength(1);
    fireEvent.change(screen.getByTestId("incident-region"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("incident-evidence-measured"));
    expect(screen.getAllByTestId("incident-row")).toHaveLength(2);
    fireEvent.click(screen.getByTestId("incident-reset"));
    expect(screen.getAllByTestId("incident-row")).toHaveLength(4);
  });

  it("an assistant command filters the panel and focuses an incident", async () => {
    render(<WorldIntelligenceView />);
    await screen.findAllByTestId("incident-row");
    act(() => {
      useIntelStore.getState().applyCommand({ action: "filter", region: "south-asia", kinds: "protest,bogus", hours: 24, min_severity: -1 });
    });
    expect(screen.getAllByTestId("incident-row")).toHaveLength(1);
    act(() => {
      useIntelStore.getState().applyCommand({ action: "focus", event_id: "gdelt:protest--2106102-20261019" });
    });
    expect(screen.getByTestId("incident-detail").getAttribute("data-incident-id")).toBe("gdelt:protest--2106102-20261019");
    act(() => {
      useIntelStore.getState().applyCommand({ action: "reset" });
    });
    expect(screen.queryByTestId("incident-detail")).toBeNull();
    expect(screen.getAllByTestId("incident-row")).toHaveLength(4);
  });

  it("sends the placed incidents to God's Eye View and keeps selection in step both ways", async () => {
    render(<WorldIntelligenceView />);
    await screen.findAllByTestId("incident-row");
    const { target, postMessage } = ready(["view", "events@1"]);
    const upsert = postMessage.mock.calls.map((c) => c[0] as Record<string, unknown>).find((m) => m.type === "gev:events-upsert")!;
    expect(upsert).toMatchObject({ v: 1, replace: true });
    const sent = upsert.events as Record<string, unknown>[];
    expect(sent.map((e) => e.id).sort()).toEqual(["gdacs:EQ-1572508", "gdelt:protest--2106102-20261019", "usgs:us6000u3bc"]);
    // Only the drawing fields cross the frame: no links, summaries or attrs.
    expect(Object.keys(sent[0]).sort()).toEqual(["category", "evidence", "id", "kind", "lat", "lon", "severity", "source", "time", "title"]);
    for (const call of postMessage.mock.calls) expect(call[1]).toBe(origin);

    // Panel → globe.
    fireEvent.click(screen.getAllByTestId("incident-row")[0]);
    expect(postMessage.mock.calls.at(-1)![0]).toMatchObject({ type: "gev:events-select", eventId: "usgs:us6000u3bc", fly: true });

    // Globe → panel, from the GEV frame only, for an id this screen sent.
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data: { type: "gev:event-selected", eventId: "gdelt:protest--2106102-20261019" }, origin: "http://evil.example", source: target }));
      window.dispatchEvent(new MessageEvent("message", { data: { type: "gev:event-selected", eventId: "made:up" }, origin, source: target }));
    });
    expect(screen.getByTestId("incident-detail").getAttribute("data-incident-id")).toBe("usgs:us6000u3bc");
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data: { type: "gev:event-selected", eventId: "gdelt:protest--2106102-20261019" }, origin, source: target }));
    });
    expect(screen.getByTestId("incident-detail").getAttribute("data-incident-id")).toBe("gdelt:protest--2106102-20261019");
    // A selection made on the globe is not flown to again.
    expect(postMessage.mock.calls.at(-1)![0]).toMatchObject({ type: "gev:events-select", fly: false });
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data: { type: "gev:event-selection-cleared" }, origin, source: target }));
    });
    expect(screen.queryByTestId("incident-detail")).toBeNull();
  });

  it("an older God's Eye View without the incident layer gets no incident messages", async () => {
    render(<WorldIntelligenceView />);
    await screen.findAllByTestId("incident-row");
    const { postMessage } = ready(["view"]);
    fireEvent.click(screen.getAllByTestId("incident-row")[0]);
    expect(postMessage.mock.calls.some((c) => String((c[0] as { type: string }).type).startsWith("gev:events"))).toBe(false);
    expect(screen.getByTestId("stark-gev-no-events")).toBeTruthy();
  });
});
