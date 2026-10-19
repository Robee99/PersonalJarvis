import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const webgl = vi.hoisted(() => ({ supported: true }));
/** What each map was asked to frame, as sorted id lists. */
const framed = vi.hoisted(() => ({ space: [] as string[][], flat: [] as string[][] }));
/** The nodes the flat map was last handed. */
const flat = vi.hoisted(() => ({ nodes: [] as { id: string; branch?: boolean }[] }));

vi.mock("@/lib/graphDimension", () => ({
  useWebglSupported: () => webgl.supported,
}));

vi.mock("react-force-graph-2d", async () => {
  const { forwardRef, useImperativeHandle } = await import("react");
  return {
    default: forwardRef(function FlatFake(props: { graphData: { nodes: { id: string; branch?: boolean }[] } }, ref) {
      flat.nodes = props.graphData.nodes;
      useImperativeHandle(ref, () => ({
        d3Force: () => undefined,
        d3ReheatSimulation: () => undefined,
        zoomToFit: (_ms: number, _pad: number, keep?: (n: { id: string }) => boolean) => {
          framed.flat.push(props.graphData.nodes.filter((n) => !keep || keep(n)).map((n) => n.id).sort());
        },
      }));
      return <div data-testid="flat-map" />;
    }),
  };
});

vi.mock("@/components/orb/MemoryGraph3D", async () => {
  const { forwardRef, useImperativeHandle } = await import("react");
  return {
    default: forwardRef(function SpaceFake(_props: unknown, ref) {
      useImperativeHandle(ref, () => ({
        fit: (ids?: ReadonlySet<string>) => framed.space.push(ids ? [...ids].sort() : []),
        focus: () => undefined,
      }));
      return <div data-testid="space-map" />;
    }),
  };
});

vi.mock("@/components/orb/hands/HandControls", () => ({
  HandOverlay: () => null,
  HandToggle: () => null,
}));

vi.mock("@/components/orb/hands/useHandTracking", () => ({
  useHandTracking: () => ({ status: "off" }),
}));

vi.mock("./MemoryOrbImport", () => ({ MemoryOrbImport: () => null }));

import { MemoryOrbView } from "./MemoryOrbView";

const graph = {
  ok: true,
  nodes: [
    { id: "s1", kind: "session", title: "Chat one", path: "sessions/s1.md" },
    { id: "s2", kind: "session", title: "Chat two", path: "sessions/s2.md" },
    { id: "i1", kind: "session", title: "Old chat", path: "imports/old/i1.md" },
    { id: "a", kind: "project", title: "Atlas", path: "projects/a.md" },
  ],
  edges: [],
};

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.startsWith("/api/wiki/graph") ? graph : url.startsWith("/api/chats") ? [] : { ok: false };
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
}

class SizedObserver {
  constructor(private cb: ResizeObserverCallback) {}
  observe() {
    this.cb([{ contentRect: { width: 800, height: 600 } } as ResizeObserverEntry], this as unknown as ResizeObserver);
  }
  disconnect() {}
  unobserve() {}
}

function renderView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryOrbView initialMode="memory" />
    </QueryClientProvider>,
  );
}

const press = (key: string, target: Window | Element = window) => act(async () => void fireEvent.keyDown(target, { key }));
const current = () => screen.getByTestId("memory-orb-section-current").textContent ?? "";

describe("MemoryOrbView sections", () => {
  beforeEach(() => {
    window.localStorage.clear();
    webgl.supported = true;
    framed.space = [];
    framed.flat = [];
    flat.nodes = [];
    stubFetch();
    vi.stubGlobal("ResizeObserver", SizedObserver);
  });

  it("steps through the sections with ] and [ and frames each one on the 3D map", async () => {
    renderView();
    await screen.findByTestId("space-map");
    await screen.findByTestId("memory-orb-sections");

    await press("]");
    expect(current()).toContain("Sessions");
    expect(current()).toContain("1/3");
    expect(framed.space.at(-1)).toEqual(["section:wiki|area:sessions", "wiki:s1", "wiki:s2"]);

    await press("]");
    expect(current()).toContain("Import · old");
    await press("]");
    expect(current()).toContain("Projects");
    // A family that is one section has no branch point: its members are framed.
    expect(framed.space.at(-1)).toEqual(["wiki:a"]);
    await press("]");
    expect(current()).toContain("Sessions");
    await press("[");
    expect(current()).toContain("Projects");

    // Two presses before the screen catches up still step twice.
    await act(async () => {
      fireEvent.keyDown(window, { key: "]" });
      fireEvent.keyDown(window, { key: "]" });
    });
    expect(current()).toContain("Import · old");

    // Typing a bracket into a text field types it; the map stays put.
    const steps = framed.space.length;
    await press("]", screen.getByLabelText("Search the memory orb"));
    expect(framed.space).toHaveLength(steps);
  });

  it("jumps to a section picked from the list, which also shows each section's colour", async () => {
    renderView();
    await screen.findByTestId("memory-orb-sections");
    await act(async () => fireEvent.click(screen.getByTestId("memory-orb-section-current")));
    const list = screen.getByTestId("memory-orb-section-list");
    const options = within(list).getAllByTestId("memory-orb-section-option");
    expect(options.map((o) => o.textContent)).toEqual(["Sessions2", "Import · old1", "Projects1"]);
    for (const option of options) {
      expect((option.querySelector("[aria-hidden]") as HTMLElement).style.background).not.toBe("");
    }

    await act(async () => fireEvent.click(options[1]));
    expect(screen.queryByTestId("memory-orb-section-list")).toBeNull();
    expect(current()).toContain("Import · old");
    expect(framed.space.at(-1)).toEqual(["section:wiki|area:imports/old", "wiki:i1"]);

    await act(async () => fireEvent.click(screen.getByTestId("memory-orb-section-next")));
    expect(current()).toContain("Projects");
  });

  it("branches the flat map's notes by section and frames a section there too", async () => {
    webgl.supported = false;
    renderView();
    await screen.findByTestId("flat-map");
    await screen.findByTestId("memory-orb-sections");
    expect(flat.nodes.filter((n) => n.branch).map((n) => n.id)).toEqual([
      "section:wiki|area:sessions",
      "section:wiki|area:imports/old",
    ]);

    await act(async () => fireEvent.click(screen.getByTestId("memory-orb-section-prev")));
    expect(current()).toContain("Projects");
    expect(framed.flat.at(-1)).toEqual(["wiki:a"]);
  });

  it("draws the family filters without a colour of their own", async () => {
    renderView();
    const filters = await screen.findByRole("group", { name: "Filter the map" });
    for (const dot of filters.querySelectorAll<HTMLElement>("button > span:first-child")) {
      expect(dot.style.background).toBe("");
    }
    expect(screen.getByTestId("memory-orb-colour-legend")).toBeTruthy();
  });
});
