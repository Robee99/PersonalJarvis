import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const webgl = vi.hoisted(() => ({ supported: true }));

vi.mock("@/lib/graphDimension", () => ({
  useWebglSupported: () => webgl.supported,
}));

vi.mock("react-force-graph-2d", async () => {
  const { forwardRef } = await import("react");
  return {
    default: forwardRef(function FlatMock(props: { graphData: { nodes: unknown[] } }, _ref) {
      return <div data-testid="flat-map" data-nodes={props.graphData.nodes.length} />;
    }),
  };
});

vi.mock("@/components/orb/MemoryGraph3D", async () => {
  const { forwardRef } = await import("react");
  return {
    default: forwardRef(function SpaceMock(props: { nodes: unknown[] }, _ref) {
      return <div data-testid="space-map" data-nodes={props.nodes.length} />;
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
    { id: "a", kind: "project", title: "Atlas" },
    { id: "b", kind: "entity", title: "Pepper" },
  ],
  edges: [{ source: "a", target: "b" }],
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

describe("MemoryOrbView map dimension", () => {
  beforeEach(() => {
    window.localStorage.clear();
    webgl.supported = true;
    stubFetch();
    vi.stubGlobal("ResizeObserver", SizedObserver);
  });

  it("opens the memory map in 3D and switches to the flat map and back", async () => {
    renderView();
    expect(await screen.findByTestId("space-map")).toBeTruthy();
    expect(screen.queryByTestId("flat-map")).toBeNull();

    await act(async () => fireEvent.click(screen.getByTestId("memory-orb-view-2d")));
    expect(await screen.findByTestId("flat-map")).toBeTruthy();
    expect(screen.queryByTestId("space-map")).toBeNull();
    expect(window.localStorage.getItem("orb2.dimension")).toBe("2d");

    await act(async () => fireEvent.click(screen.getByTestId("memory-orb-view-3d")));
    expect(await screen.findByTestId("space-map")).toBeTruthy();
  });

  it("falls back to the flat map when WebGL is unavailable", async () => {
    webgl.supported = false;
    renderView();
    expect(await screen.findByTestId("flat-map")).toBeTruthy();
    expect((screen.getByTestId("memory-orb-view-3d") as HTMLButtonElement).disabled).toBe(true);
  });
});
