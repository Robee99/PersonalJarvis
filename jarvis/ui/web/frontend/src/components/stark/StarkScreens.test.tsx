import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

/**
 * The Stark screens' own parts: the task list against its endpoint, the
 * Intelligence and Orbital screens against the public-data proxy (live,
 * stale and missing sources), the rail's navigation, and the helpers that
 * turn public data into severities, regions and levels. The command bar and
 * the WebGL globe have their own tests and are stubbed here.
 */
vi.mock("@/components/stark/CommandBar", () => ({ CommandBar: () => <div data-testid="stark-command-bar" /> }));
vi.mock("@/components/stark/OrbitGlobe", () => ({ OrbitGlobe: () => <div data-testid="stark-orbit-globe" /> }));

import { TasksPanel, type Todo } from "@/components/stark/TasksPanel";
import { IntelligenceView, activityLevel } from "@/views/IntelligenceView";
import { OrbitalView, distanceKm } from "@/views/OrbitalView";
import { agoMs, eventSeverity, feedState, kpLevel, quakeSeverity, regionOf } from "@/lib/publicData";
import { useI18nStore } from "@/i18n";
import { useEventStore } from "@/store/events";

type Handler = (url: string, init?: RequestInit) => Response | undefined;

function stubFetch(handler: Handler) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      return handler(url, init) ?? new Response("{}", { status: 404 });
    }),
  );
}

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

function withClient(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

const META = { available: true, stale: false, fetched_at: Date.now() / 1000, source: "Test", source_url: "https://example.org" };

beforeEach(() => {
  window.localStorage.clear();
  useI18nStore.getState().setUi("en", { push: false });
  useEventStore.setState({ connected: true, voiceState: "idle", assistantName: "Jarvis", thinkingSteps: [], activeSection: "intelligence" });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("CURRENT TASKS", () => {
  function todoServer(initial: Todo[] = []) {
    const todos = [...initial];
    stubFetch((url, init) => {
      const method = init?.method ?? "GET";
      if (url.startsWith("/api/tasks")) return ok({ tasks: [] });
      if (url === "/api/deck/todos" && method === "GET") return ok({ todos });
      if (url === "/api/deck/todos" && method === "POST") {
        const { title } = JSON.parse(String(init?.body)) as { title: string };
        const todo: Todo = { id: `t${todos.length + 1}`, title, done: false, created_ms: Date.now(), done_ms: null };
        todos.push(todo);
        return ok({ todo });
      }
      const id = decodeURIComponent(url.split("/").pop() ?? "");
      const at = todos.findIndex((x) => x.id === id);
      if (at < 0) return new Response(JSON.stringify({ detail: "No such to-do." }), { status: 404 });
      if (method === "PATCH") {
        const { done } = JSON.parse(String(init?.body)) as { done: boolean };
        todos[at] = { ...todos[at], done, done_ms: done ? Date.now() : null };
        return ok({ todo: todos[at] });
      }
      if (method === "DELETE") {
        todos.splice(at, 1);
        return ok({ deleted: id });
      }
      return undefined;
    });
    return todos;
  }

  test("an empty list says so; + adds a to-do, the box ticks it done, × dismisses it", async () => {
    const todos = todoServer();
    withClient(<TasksPanel />);
    expect(await screen.findByTestId("stark-tasks-empty")).toBeTruthy();

    fireEvent.click(screen.getByTestId("stark-tasks-add"));
    const form = screen.getByTestId("stark-tasks-form");
    fireEvent.change(within(form).getByRole("textbox"), { target: { value: "Call the vendor" } });
    fireEvent.submit(form);
    const row = await screen.findByTestId("stark-todo");
    expect(row.textContent).toContain("Call the vendor");
    expect(screen.getByTestId("stark-tasks-count").textContent).toBe("1");

    fireEvent.click(within(row).getByTestId("stark-todo-check"));
    await waitFor(() => expect(screen.getByTestId("stark-todo").getAttribute("data-done")).toBe("true"));
    expect(todos[0].done).toBe(true);

    fireEvent.click(within(screen.getByTestId("stark-todo")).getByTestId("stark-todo-dismiss"));
    await waitFor(() => expect(screen.queryByTestId("stark-todo")).toBeNull());
    expect(todos).toEqual([]);
  });

  test("a list that cannot be read says so instead of showing nothing", async () => {
    stubFetch((url) => (url === "/api/deck/todos" ? new Response("boom", { status: 500 }) : undefined));
    withClient(<TasksPanel />);
    expect(await screen.findByTestId("stark-tasks-error")).toBeTruthy();
  });
});

describe("the Intelligence screen", () => {
  const now = Date.now();
  const quakes = {
    ...META,
    source: "USGS",
    items: [
      { id: "a", mag: 6.4, place: "off Japan", time_ms: now - 60_000, lon: 142, lat: 38, depth_km: 20, url: null, alert: null, tsunami: false, sig: 600 },
      { id: "b", mag: 4.1, place: "Nevada", time_ms: now - 3_600_000, lon: -117, lat: 38, depth_km: 8, url: null, alert: null, tsunami: false, sig: 100 },
    ],
  };
  const events = {
    ...META,
    source: "NASA EONET",
    items: [
      { id: "v", title: "Etna eruption", category: "Volcanoes", category_id: "volcanoes", lon: 15, lat: 37.7, date_ms: now - 7_200_000, link: null, magnitude: null, unit: null },
    ],
  };

  test("live sources fill the alerts (medium and up) and the source list; a layer toggle hides its events", async () => {
    stubFetch((url) => {
      if (url === "/api/public/quakes") return ok(quakes);
      if (url === "/api/public/events") return ok(events);
      return undefined;
    });
    withClient(<IntelligenceView />);
    // The M4.1 is on the map but too small to be an alert.
    await waitFor(() => expect(screen.getAllByTestId("stark-alert").length).toBe(2));
    // Highest severity first: the M6.4 leads.
    expect(screen.getAllByTestId("stark-alert")[0].textContent).toContain("M6.4");
    const sources = screen.getAllByTestId("stark-source");
    expect(sources.find((s) => s.textContent?.includes("USGS"))?.getAttribute("data-state")).toBe("live");

    fireEvent.click(screen.getByTestId("stark-layer-volcano"));
    expect(screen.getByTestId("stark-layer-volcano").getAttribute("aria-pressed")).toBe("false");
    await waitFor(() => expect(screen.getAllByTestId("stark-alert").length).toBe(1));
  });

  test("with every source down the map says there is no data, never a made-up picture", async () => {
    stubFetch((url) => (url.startsWith("/api/public/") ? ok({ ...META, available: false, items: [] }) : undefined));
    withClient(<IntelligenceView />);
    expect(await screen.findByTestId("stark-intel-empty")).toBeTruthy();
    expect(screen.queryAllByTestId("stark-alert")).toEqual([]);
    await waitFor(() =>
      expect(screen.getAllByTestId("stark-source").filter((s) => s.getAttribute("data-state") === "down").length).toBeGreaterThan(0),
    );
  });

  test("an old answer is marked stale, not live", async () => {
    stubFetch((url) => {
      if (url === "/api/public/quakes") return ok({ ...quakes, stale: true });
      if (url === "/api/public/events") return ok(events);
      return undefined;
    });
    withClient(<IntelligenceView />);
    await waitFor(() =>
      expect(screen.getAllByTestId("stark-source").find((s) => s.textContent?.includes("USGS"))?.getAttribute("data-state")).toBe("stale"),
    );
  });
});

describe("the Orbital screen", () => {
  test("an ISS tracker that does not answer says so; the other panels still read out", async () => {
    stubFetch((url) => {
      if (url === "/api/public/iss") return ok({ ...META, available: false });
      if (url === "/api/public/space")
        return ok({ ...META, kp: { value: 5.3, time_ms: Date.now(), series: [] }, f107: { value: 150, time_ms: Date.now() }, neos: [], failed: [] });
      if (url === "/api/public/stations") return ok({ ...META, items: [] });
      return undefined;
    });
    withClient(<OrbitalView />);
    expect(await screen.findByText("The ISS tracker did not answer.")).toBeTruthy();
    expect(await screen.findByText(/G1/)).toBeTruthy();
    expect(screen.getByText("No close approaches in the next 60 days.")).toBeTruthy();
    expect(screen.getByTestId("stark-orbit-feed").getAttribute("data-state")).toBe("down");
  });
});

describe("the rail", () => {
  test("moves between the Stark screens and marks the one on screen", async () => {
    stubFetch(() => undefined);
    withClient(<IntelligenceView />);
    expect(screen.getByTestId("stark-rail-intelligence").getAttribute("aria-current")).toBe("page");
    act(() => {
      fireEvent.click(screen.getByTestId("stark-rail-orbital"));
    });
    expect(useEventStore.getState().activeSection).toBe("orbital");
    act(() => {
      fireEvent.click(screen.getByTestId("stark-rail-command"));
    });
    expect(useEventStore.getState().activeSection).toBe("deck");
  });
});

describe("public-data helpers", () => {
  test("a source's state: live, stale, down or still loading", () => {
    expect(feedState({ data: META, isError: false, isPending: false })).toBe("live");
    expect(feedState({ data: { ...META, stale: true }, isError: false, isPending: false })).toBe("stale");
    expect(feedState({ data: { ...META, available: false }, isError: false, isPending: false })).toBe("down");
    expect(feedState({ isError: true, isPending: false })).toBe("down");
    expect(feedState({ isError: false, isPending: true })).toBe("loading");
  });

  test("ages read as now, minutes, hours and days", () => {
    const now = 10 * 86_400_000;
    expect(agoMs(now - 5_000, now)).toBe("now");
    expect(agoMs(now - 12 * 60_000, now)).toBe("12m");
    expect(agoMs(now - 3 * 3_600_000, now)).toBe("3h");
    expect(agoMs(now - 2 * 86_400_000, now)).toBe("2d");
    expect(agoMs(null, now)).toBe("—");
  });

  test("regions by coordinate", () => {
    expect(regionOf(51, 35)).toBe("middle_east");
    expect(regionOf(2, 48)).toBe("europe");
    expect(regionOf(30, -10)).toBe("africa");
    expect(regionOf(-100, 40)).toBe("north_america");
    expect(regionOf(-60, -20)).toBe("south_america");
    expect(regionOf(100, 40)).toBe("asia");
    expect(regionOf(150, -30)).toBe("indo_pacific");
    expect(regionOf(-30, 40)).toBeNull();
  });

  test("severity from the source's own alert, magnitude or storm wind", () => {
    expect(quakeSeverity({ mag: 6.1, alert: null })).toBe("high");
    expect(quakeSeverity({ mag: 4, alert: "orange" })).toBe("high");
    expect(quakeSeverity({ mag: 5.2, alert: null })).toBe("med");
    expect(quakeSeverity({ mag: 3, alert: null })).toBe("low");
    expect(eventSeverity({ category_id: "severeStorms", magnitude: 90, unit: "kts" })).toBe("high");
    expect(eventSeverity({ category_id: "severeStorms", magnitude: 40, unit: "kts" })).toBe("med");
    expect(eventSeverity({ category_id: "volcanoes", magnitude: null, unit: null })).toBe("med");
    expect(eventSeverity({ category_id: "wildfires", magnitude: null, unit: null })).toBe("low");
  });

  test("Kp in NOAA's words, and the screen's activity level", () => {
    expect(kpLevel(2)).toEqual({ key: "quiet", storm: null });
    expect(kpLevel(3.3)).toEqual({ key: "unsettled", storm: null });
    expect(kpLevel(4)).toEqual({ key: "active", storm: null });
    expect(kpLevel(5.3)).toEqual({ key: "storm", storm: "G1" });
    expect(kpLevel(9)).toEqual({ key: "storm", storm: "G5" });
    expect(activityLevel([]).level).toBe("low");
    expect(activityLevel([{ severity: "med" }, { severity: "low" }]).level).toBe("moderate");
    expect(activityLevel([{ severity: "high" }]).level).toBe("high");
    expect(activityLevel([{ severity: "high" }, { severity: "high" }]).level).toBe("critical");
  });

  test("great-circle distance", () => {
    // London to Paris is about 344 km.
    expect(distanceKm({ lat: 51.5074, lon: -0.1278 }, { lat: 48.8566, lon: 2.3522 })).toBeGreaterThan(330);
    expect(distanceKm({ lat: 51.5074, lon: -0.1278 }, { lat: 48.8566, lon: 2.3522 })).toBeLessThan(355);
  });
});
