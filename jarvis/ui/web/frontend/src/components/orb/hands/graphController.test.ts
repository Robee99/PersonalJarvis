import { describe, expect, it, vi } from "vitest";

import { HAND_REACH_PX, graphHandController, nodeAt, type GraphCamera } from "./graphController";

/** A camera at zoom k centred on (cx, cy) over an 800×600 canvas. */
function fakeGraph(k = 2, cx = 0, cy = 0) {
  const state = { k, cx, cy };
  const graph = {
    screen2GraphCoords: (x: number, y: number) => ({ x: (x - 400) / state.k + state.cx, y: (y - 300) / state.k + state.cy }),
    centerAt: vi.fn((x?: number, y?: number) => {
      if (x === undefined) return { x: state.cx, y: state.cy };
      state.cx = x;
      state.cy = y as number;
      return graph;
    }),
    zoom: vi.fn((next?: number) => {
      if (next === undefined) return state.k;
      state.k = next;
      return graph;
    }),
  };
  return { graph: graph as unknown as GraphCamera, state };
}

const nodes = [
  { id: "a", x: 0, y: 0 },
  { id: "b", x: 100, y: 0 },
];

function target(graph: GraphCamera, picked: { id: string } | null = null) {
  return {
    graph: () => graph,
    size: () => ({ width: 800, height: 600 }),
    nodes: () => nodes,
    picked: () => picked as never,
    hover: vi.fn(),
    pick: vi.fn(),
    open: vi.fn(),
  };
}

describe("gestures on the force graph", () => {
  it("finds the node under the hand within a finger's reach", () => {
    const { graph } = fakeGraph(2);
    expect(nodeAt(target(graph), { x: 0.5, y: 0.5 })?.id).toBe("a");
    // b sits at x=100 → screen 600px = 0.75 of the width.
    expect(nodeAt(target(graph), { x: 0.75, y: 0.5 })?.id).toBe("b");
    const off = 0.5 + (HAND_REACH_PX + 4) / 800;
    expect(nodeAt(target(graph), { x: off, y: 0.5 })).toBeNull();
  });

  it("hover, pinch to pick, pinch again to open", () => {
    const { graph } = fakeGraph(2);
    const t = target(graph);
    const control = graphHandController(t);
    control.onAction({ type: "hover", point: { x: 0.5, y: 0.5 } });
    expect(t.hover).toHaveBeenCalledWith(nodes[0]);
    control.onAction({ type: "select", point: { x: 0.5, y: 0.5 } });
    expect(t.pick).toHaveBeenCalledWith(nodes[0]);
    const again = target(graph, nodes[0]);
    graphHandController(again).onAction({ type: "select", point: { x: 0.5, y: 0.5 } });
    expect(again.open).toHaveBeenCalledWith(nodes[0]);
    expect(again.pick).not.toHaveBeenCalled();
  });

  it("a pinch on empty space clears the pick", () => {
    const { graph } = fakeGraph(2);
    const t = target(graph);
    graphHandController(t).onAction({ type: "select", point: { x: 0.1, y: 0.1 } });
    expect(t.pick).toHaveBeenCalledWith(null);
  });

  it("an open-palm drag moves the map with the hand, a two-hand pinch zooms", () => {
    const { graph, state } = fakeGraph(2, 10, 10);
    const control = graphHandController(target(graph));
    control.onAction({ type: "drag", dx: 0.1, dy: 0 });
    // 0.1 of 800px at zoom 2 = 40 graph units; the camera moves against the hand.
    expect(state.cx).toBeCloseTo(-30);
    expect(state.cy).toBeCloseTo(10);
    control.onAction({ type: "zoom", scale: 1.5 });
    expect(state.k).toBeCloseTo(3);
    control.onAction({ type: "zoom", scale: 100 });
    expect(state.k).toBe(14);
  });
});
