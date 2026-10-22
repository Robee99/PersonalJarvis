import { describe, expect, it } from "vitest";

import { GestureEngine, HOLD_MS, synthHand, type HandLandmarks } from "./gestures";
import {
  HAND_REACH_PX,
  graphHandController,
  handMapController,
  nodeAt,
  sceneBridge,
  wheelDeltaFor,
  type GraphCamera,
  type GraphHandTarget,
  type HandMap,
} from "./graphController";

/** A camera at zoom k centred on (cx, cy) over an 800×600 canvas. */
function fakeGraph(k = 2, cx = 0, cy = 0) {
  const state = { k, cx, cy, fits: 0 };
  const graph: GraphCamera = {
    screen2GraphCoords: (x: number, y: number) => ({ x: (x - 400) / state.k + state.cx, y: (y - 300) / state.k + state.cy }),
    centerAt: ((x?: number, y?: number) => {
      if (x === undefined) return { x: state.cx, y: state.cy };
      state.cx = x;
      state.cy = y as number;
      return graph;
    }) as GraphCamera["centerAt"],
    zoom: ((next?: number) => {
      if (next === undefined) return state.k;
      state.k = next;
      return graph;
    }) as GraphCamera["zoom"],
    zoomToFit: () => {
      state.fits += 1;
      state.k = 1;
      state.cx = 0;
      state.cy = 0;
    },
  };
  return { graph, state };
}

interface Node {
  id: string;
  x: number;
  y: number;
}

const nodes: Node[] = [
  { id: "a", x: 0, y: 0 },
  { id: "b", x: 100, y: 0 },
];

/** A fake view: records what hands did to it, keeps its own pick like the real one. */
function fakeTarget(graph: GraphCamera, extra: Partial<GraphHandTarget<Node>> = {}) {
  const log = { hovered: [] as (string | null)[], picked: [] as (string | null)[], opened: [] as string[] };
  let picked: Node | null = null;
  const target: GraphHandTarget<Node> = {
    graph: () => graph,
    size: () => ({ width: 800, height: 600 }),
    nodes: () => nodes,
    picked: () => picked,
    hover: (node) => log.hovered.push(node?.id ?? null),
    pick: (node) => {
      picked = node;
      log.picked.push(node?.id ?? null);
    },
    open: (node) => log.opened.push(node.id),
    ...extra,
  };
  return { target, log, setPicked: (node: Node | null) => (picked = node) };
}

/** Screen fraction of a graph point for the fake camera. */
const screenOf = (state: { k: number; cx: number; cy: number }, n: { x: number; y: number }) => ({
  x: ((n.x - state.cx) * state.k + 400) / 800,
  y: ((n.y - state.cy) * state.k + 300) / 600,
});

describe("finding the node under a hand", () => {
  it("snaps to the nearest node within a finger's reach, and nothing beyond it", () => {
    const { graph } = fakeGraph(2);
    const { target } = fakeTarget(graph);
    expect(nodeAt(target, { x: 0.5, y: 0.5 })?.id).toBe("a");
    // b sits at x=100 → screen 600px = 0.75 of the width.
    expect(nodeAt(target, { x: 0.75, y: 0.5 })?.id).toBe("b");
    expect(nodeAt(target, { x: 0.5 + (HAND_REACH_PX - 4) / 800, y: 0.5 })?.id).toBe("a");
    expect(nodeAt(target, { x: 0.5 + (HAND_REACH_PX + 4) / 800, y: 0.5 })).toBeNull();
  });

  it("measures the reach in screen pixels at any zoom", () => {
    const { graph, state } = fakeGraph(0.5);
    const { target } = fakeTarget(graph);
    // Left of a, away from b (which sits 50px to its right at this zoom).
    expect(nodeAt(target, { x: 0.5 - (HAND_REACH_PX - 4) / 800, y: 0.5 })?.id).toBe("a");
    expect(nodeAt(target, { x: 0.5 - (HAND_REACH_PX + 4) / 800, y: 0.5 })).toBeNull();
    state.k = 8;
    expect(nodeAt(target, { x: 0.5 + (HAND_REACH_PX + 4) / 800, y: 0.5 })).toBeNull();
  });
});

describe("gesture actions on the force graph", () => {
  it("hover, pinch to pick, pinch the picked node to open", () => {
    const { graph } = fakeGraph(2);
    const { target, log } = fakeTarget(graph);
    const control = graphHandController(target);
    control.onAction({ type: "hover", point: { x: 0.5, y: 0.5 } });
    expect(log.hovered).toEqual(["a"]);
    control.onAction({ type: "select", point: { x: 0.5, y: 0.5 } });
    expect(log.picked).toEqual(["a"]);
    control.onAction({ type: "select", point: { x: 0.51, y: 0.5 } });
    expect(log.opened).toEqual(["a"]);
    expect(log.picked).toEqual(["a"]);
  });

  it("a pinch on empty space clears the pick", () => {
    const { graph } = fakeGraph(2);
    const { target, log } = fakeTarget(graph);
    graphHandController(target).onAction({ type: "select", point: { x: 0.1, y: 0.1 } });
    expect(log.picked).toEqual([null]);
  });

  it("a pinch hold opens the node under the hand, or else the picked one", () => {
    const { graph } = fakeGraph(2);
    const { target, log, setPicked } = fakeTarget(graph);
    const control = graphHandController(target);
    control.onAction({ type: "open", point: { x: 0.75, y: 0.5 } });
    expect(log.opened).toEqual(["b"]);
    setPicked(nodes[0]);
    control.onAction({ type: "open", point: { x: 0.1, y: 0.1 } });
    expect(log.opened).toEqual(["b", "a"]);
  });

  it("thumbs up opens the picked node, else the hovered one, else nothing", () => {
    const { graph } = fakeGraph(2);
    const { target, log, setPicked } = fakeTarget(graph);
    const control = graphHandController(target);
    control.onAction({ type: "confirm" });
    expect(log.opened).toEqual([]);
    control.onAction({ type: "hover", point: { x: 0.75, y: 0.5 } });
    control.onAction({ type: "confirm" });
    expect(log.opened).toEqual(["b"]);
    setPicked(nodes[0]);
    control.onAction({ type: "confirm" });
    expect(log.opened).toEqual(["b", "a"]);
  });

  it("a drag moves the map with the hand, a two-hand pinch zooms within limits", () => {
    const { graph, state } = fakeGraph(2, 10, 10);
    const control = graphHandController(fakeTarget(graph).target);
    control.onAction({ type: "drag", dx: 0.1, dy: 0 });
    // 0.1 of 800px at zoom 2 = 40 graph units; the camera moves against the hand.
    expect(state.cx).toBeCloseTo(-30);
    expect(state.cy).toBeCloseTo(10);
    control.onAction({ type: "zoom", scale: 1.5 });
    expect(state.k).toBeCloseTo(3);
    control.onAction({ type: "zoom", scale: 100 });
    expect(state.k).toBe(14);
    control.onAction({ type: "zoom", scale: 0.0001 });
    expect(state.k).toBe(0.15);
  });

  it("reset clears hover and pick and frames the map, through the view's own reset when it has one", () => {
    const { graph, state } = fakeGraph(4, 50, 50);
    const plain = fakeTarget(graph);
    graphHandController(plain.target).onAction({ type: "reset" });
    expect(state.fits).toBe(1);
    expect(plain.log.picked).toEqual([null]);
    expect(plain.log.hovered).toEqual([null]);

    let resets = 0;
    const own = fakeTarget(graph, { reset: () => (resets += 1) });
    graphHandController(own.target).onAction({ type: "reset" });
    expect(resets).toBe(1);
    expect(state.fits).toBe(1);
  });

  it("a swipe steps the view's sections, and is harmless without them", () => {
    const steps: number[] = [];
    const { graph } = fakeGraph();
    graphHandController(fakeTarget(graph, { section: (s) => steps.push(s) }).target).onAction({ type: "section", step: -1 });
    expect(steps).toEqual([-1]);
    expect(() => graphHandController(fakeTarget(graph).target).onAction({ type: "section", step: 1 })).not.toThrow();
  });

  it("does nothing before the graph exists", () => {
    const { target, log } = fakeTarget(undefined as unknown as GraphCamera);
    const control = graphHandController(target);
    for (const action of [
      { type: "hover", point: { x: 0.5, y: 0.5 } },
      { type: "select", point: { x: 0.5, y: 0.5 } },
      { type: "drag", dx: 0.1, dy: 0.1 },
      { type: "zoom", scale: 2 },
      { type: "reset" },
    ] as const)
      control.onAction(action);
    expect(log.opened).toEqual([]);
  });
});

describe("a map with only the basics", () => {
  it("takes every gesture without pan, zoom, section or reset handlers", () => {
    const opened: string[] = [];
    let picked: { id: string } | null = null;
    const map: HandMap<{ id: string }> = {
      nodeAt: () => ({ id: "n" }),
      picked: () => picked,
      hover: () => undefined,
      pick: (node) => (picked = node),
      open: (node) => opened.push(node.id),
    };
    const control = handMapController(map);
    for (const action of [
      { type: "drag", dx: 0.1, dy: 0 },
      { type: "zoom", scale: 2 },
      { type: "section", step: 1 },
      { type: "release" },
    ] as const)
      control.onAction(action);
    control.onAction({ type: "select", point: { x: 0.5, y: 0.5 } });
    control.onAction({ type: "select", point: { x: 0.5, y: 0.5 } });
    expect(opened).toEqual(["n"]);
    control.onAction({ type: "reset" });
    expect(picked).toBeNull();
  });
});

describe("hands end to end on the force graph", () => {
  const FRAME_MS = 33;
  /** The hand (in camera coordinates) whose fingertip cursor lands on a screen point. */
  const handOver = (target: { x: number; y: number }, pose: "point" | "pinch" | "thumbs" | "peace") => {
    // Fingertip offset of the synthetic pointing hand, mirrored and with the reach gain.
    const probe = synthHand(0.5, 0.5, "point");
    const tip = { x: probe[8].x - 0.5, y: probe[8].y - 0.5 };
    const cam = { x: 1 - (0.5 + (target.x - 0.5) / 1.45), y: 0.5 + (target.y - 0.5) / 1.45 };
    return synthHand(cam.x - tip.x, cam.y - tip.y, pose);
  };

  function drive(frames: HandLandmarks[][], control: ReturnType<typeof graphHandController>, engine = new GestureEngine(), start = 1000) {
    frames.forEach((hands, i) => {
      for (const action of engine.update(hands, start + i * FRAME_MS).actions) control.onAction(action);
    });
    return engine;
  }

  const repeat = <T,>(n: number, make: () => T): T[] => Array.from({ length: n }, make);

  it("point at a node, pinch to pick it, pinch again to open it", () => {
    const { graph, state } = fakeGraph(2);
    const { target, log } = fakeTarget(graph);
    const control = graphHandController(target);
    const over = screenOf(state, nodes[1]);
    const point = () => [handOver(over, "point")];
    const pinch = () => [handOver(over, "pinch")];
    drive([...repeat(6, point), ...repeat(5, pinch), ...repeat(6, point), ...repeat(5, pinch), ...repeat(4, point)], control);
    expect(log.hovered.at(-1)).toBe("b");
    expect(log.picked).toEqual(["b"]);
    expect(log.opened).toEqual(["b"]);
  });

  it("pinch and hold opens the node at once", () => {
    const { graph, state } = fakeGraph(2);
    const { target, log } = fakeTarget(graph);
    const over = screenOf(state, nodes[0]);
    drive(
      [...repeat(6, () => [handOver(over, "point")]), ...repeat(Math.ceil(HOLD_MS / FRAME_MS) + 3, () => [handOver(over, "pinch")])],
      graphHandController(target),
    );
    expect(log.opened).toEqual(["a"]);
    expect(log.picked).toEqual([]);
  });

  it("point, thumbs up to open what was hovered; peace to reset", () => {
    const { graph, state } = fakeGraph(3, 40, 0);
    const { target, log } = fakeTarget(graph);
    const control = graphHandController(target);
    const over = screenOf(state, nodes[1]);
    drive(
      [
        ...repeat(6, () => [handOver(over, "point")]),
        ...repeat(18, () => [handOver(over, "thumbs")]),
        ...repeat(18, () => [handOver(over, "peace")]),
      ],
      control,
    );
    expect(log.opened).toEqual(["b"]);
    expect(state.fits).toBe(1);
    expect(log.picked.at(-1)).toBeNull();
  });
});

describe("the 3D map's pointer and wheel", () => {
  function scene() {
    const container = document.createElement("div");
    const canvas = document.createElement("canvas");
    container.appendChild(canvas);
    document.body.appendChild(container);
    canvas.getBoundingClientRect = () => ({ left: 10, top: 20, width: 400, height: 300, right: 410, bottom: 320, x: 10, y: 20, toJSON: () => ({}) });
    const moves: { x: number; y: number; type: string }[] = [];
    const wheels: number[] = [];
    container.addEventListener("pointermove", (e) => moves.push({ x: e.clientX, y: e.clientY, type: e.type }));
    canvas.addEventListener("wheel", (e) => wheels.push(e.deltaY));
    return { canvas, moves, wheels, done: () => container.remove() };
  }

  it("moves the map's pointer to the hand and zooms through the wheel", () => {
    const s = scene();
    const bridge = sceneBridge(() => s.canvas);
    expect(bridge.move({ x: 0.5, y: 0.25 })).toBe(true);
    expect(s.moves).toEqual([{ x: 210, y: 95, type: "pointermove" }]);
    expect(bridge.zoom(1.2)).toBe(true);
    expect(bridge.zoom(1 / 1.2)).toBe(true);
    expect(s.wheels[0]).toBeLessThan(0); // wheel up: closer
    expect(s.wheels[1]).toBeCloseTo(-s.wheels[0]);
    s.done();
  });

  it("does nothing while the 3D map has no canvas", () => {
    const bridge = sceneBridge(() => null);
    expect(bridge.move({ x: 0.5, y: 0.5 })).toBe(false);
    expect(bridge.zoom(2)).toBe(false);
  });

  it("asks the orbit controls for exactly the zoom the hands made", () => {
    for (const scale of [0.5, 0.9, 1.1, 2]) {
      const d = wheelDeltaFor(scale);
      // OrbitControls dollies by 0.95^(|deltaY|/100): in (camera closer) for d < 0.
      const dolly = Math.pow(0.95, Math.abs(d) / 100);
      expect(d < 0 ? 1 / dolly : dolly).toBeCloseTo(scale, 6);
    }
    expect(wheelDeltaFor(1)).toBeCloseTo(0);
    expect(wheelDeltaFor(0)).toBe(0);
  });
});
