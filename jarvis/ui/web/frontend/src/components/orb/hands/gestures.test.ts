import { describe, expect, it } from "vitest";

import {
  GestureEngine,
  PINCH_IN,
  PINCH_OUT,
  handMetrics,
  synthHand,
  toScreen,
  type GestureAction,
} from "./gestures";

/** Feed frames 33 ms apart (30 fps) and collect every action. */
function run(engine: GestureEngine, frames: ReturnType<typeof synthHand>[][], start = 1000) {
  const actions: GestureAction[] = [];
  frames.forEach((hands, i) => actions.push(...engine.update(hands, start + i * 33).actions));
  return actions;
}

const repeat = <T,>(n: number, make: (i: number) => T): T[] => Array.from({ length: n }, (_, i) => make(i));
const of = (actions: GestureAction[], type: GestureAction["type"]) => actions.filter((a) => a.type === type);

describe("hand metrics (the Hands view's own thresholds)", () => {
  it("tells an open palm, a pinch, a pointing finger and a fist apart", () => {
    const open = handMetrics(synthHand(0.5, 0.5, "open"));
    const pinch = handMetrics(synthHand(0.5, 0.5, "pinch"));
    const point = handMetrics(synthHand(0.5, 0.5, "point"));
    const fist = handMetrics(synthHand(0.5, 0.5, "fist"));
    expect(open.open && open.pinch > PINCH_OUT).toBe(true);
    expect(pinch.pinch).toBeLessThan(PINCH_IN);
    expect(pinch.open).toBe(false);
    expect(point.point).toBe(true);
    expect(point.open).toBe(false);
    expect(fist.fist).toBe(true);
  });

  it("mirrors the camera and reaches the edges from a central zone", () => {
    expect(toScreen({ x: 0.5, y: 0.5 })).toEqual({ x: 0.5, y: 0.5 });
    // The hand on the camera's left is on the person's right.
    expect(toScreen({ x: 0.2, y: 0.5 }).x).toBeGreaterThan(0.5);
    expect(toScreen({ x: 0.0, y: 1.0 })).toEqual({ x: 1, y: 1 });
  });
});

describe("gesture → Orb action mapping", () => {
  it("a pointing hand hovers at its fingertip once it has been seen a few frames", () => {
    const engine = new GestureEngine();
    const actions = run(engine, repeat(6, () => [synthHand(0.3, 0.5, "point")]));
    const hovers = of(actions, "hover");
    expect(hovers.length).toBe(4); // frames 3..6
    const last = hovers.at(-1);
    expect(last?.type === "hover" && last.point.x).toBeGreaterThan(0.6); // mirrored
    expect(of(actions, "select")).toHaveLength(0);
  });

  it("a quick, still pinch selects at the pinch point", () => {
    const engine = new GestureEngine();
    const actions = run(engine, [
      ...repeat(3, () => [synthHand(0.4, 0.4, "point")]),
      ...repeat(4, () => [synthHand(0.4, 0.4, "pinch")]),
      ...repeat(2, () => [synthHand(0.4, 0.4, "point")]),
    ]);
    const selects = of(actions, "select");
    expect(selects).toHaveLength(1);
    const sel = selects[0];
    expect(sel.type === "select" && Math.abs(sel.point.x - 0.6) < 0.1).toBe(true);
  });

  it("a one-frame near-pinch is not a selection (it must be earned)", () => {
    const engine = new GestureEngine();
    const actions = run(engine, [
      ...repeat(3, () => [synthHand(0.4, 0.4, "point")]),
      [synthHand(0.4, 0.4, "pinch")],
      ...repeat(3, () => [synthHand(0.4, 0.4, "point")]),
    ]);
    expect(of(actions, "select")).toHaveLength(0);
  });

  it("a long or travelling pinch is not a tap", () => {
    const held = run(new GestureEngine(), [
      ...repeat(3, () => [synthHand(0.4, 0.4, "point")]),
      ...repeat(20, () => [synthHand(0.4, 0.4, "pinch")]), // ~660 ms
      [synthHand(0.4, 0.4, "point")],
    ]);
    expect(of(held, "select")).toHaveLength(0);
    const moved = run(new GestureEngine(), [
      ...repeat(3, () => [synthHand(0.4, 0.4, "point")]),
      ...repeat(6, (i) => [synthHand(0.4 + i * 0.03, 0.4, "pinch")]),
      [synthHand(0.55, 0.4, "point")],
    ]);
    expect(of(moved, "select")).toHaveLength(0);
  });

  it("an open palm moving right drags left on the mirrored map, and lets go", () => {
    const engine = new GestureEngine();
    const actions = run(engine, [
      ...repeat(8, (i) => [synthHand(0.4 + i * 0.02, 0.5, "open")]),
      [synthHand(0.56, 0.5, "fist")],
    ]);
    const drags = of(actions, "drag");
    expect(drags.length).toBeGreaterThanOrEqual(5);
    for (const d of drags) expect(d.type === "drag" && d.dx).toBeLessThan(0);
    expect(of(actions, "release")).toHaveLength(1);
    expect(of(actions, "hover").length).toBeGreaterThan(0); // the fist hovers again
  });

  it("a still open palm does not drag", () => {
    const actions = run(new GestureEngine(), repeat(8, () => [synthHand(0.4, 0.5, "open")]));
    expect(of(actions, "drag")).toHaveLength(0);
  });

  it("two pinching hands moving apart zoom in, together zoom out, and select nothing", () => {
    const apart = run(new GestureEngine(), [
      ...repeat(2, () => [synthHand(0.4, 0.5, "point"), synthHand(0.6, 0.5, "point")]),
      ...repeat(10, (i) => [synthHand(0.4 - i * 0.01, 0.5, "pinch"), synthHand(0.6 + i * 0.01, 0.5, "pinch")]),
      [synthHand(0.3, 0.5, "point"), synthHand(0.7, 0.5, "point")],
    ]);
    const zooms = of(apart, "zoom");
    expect(zooms.length).toBeGreaterThan(3);
    const total = zooms.reduce((acc, z) => acc * (z.type === "zoom" ? z.scale : 1), 1);
    expect(total).toBeGreaterThan(1.5);
    expect(of(apart, "select")).toHaveLength(0);

    const together = run(new GestureEngine(), [
      ...repeat(2, () => [synthHand(0.3, 0.5, "point"), synthHand(0.7, 0.5, "point")]),
      ...repeat(10, (i) => [synthHand(0.3 + i * 0.01, 0.5, "pinch"), synthHand(0.7 - i * 0.01, 0.5, "pinch")]),
    ]);
    const shrink = of(together, "zoom").reduce((acc, z) => acc * (z.type === "zoom" ? z.scale : 1), 1);
    expect(shrink).toBeLessThan(0.75);
  });

  it("keeps each hand's state when MediaPipe swaps their order", () => {
    const engine = new GestureEngine();
    const left = (pose: "point" | "pinch") => synthHand(0.3, 0.5, pose);
    const right = () => synthHand(0.7, 0.5, "point");
    const actions = run(engine, [
      ...repeat(3, () => [left("point"), right()]),
      [left("pinch"), right()],
      [right(), left("pinch")], // order swapped: still the same pinching hand
      [left("pinch"), right()],
      [right(), left("point")],
    ]);
    expect(of(actions, "select")).toHaveLength(1);
  });

  it("losing a pinching hand releases", () => {
    const engine = new GestureEngine();
    const actions = run(engine, [...repeat(4, () => [synthHand(0.4, 0.4, "pinch")]), []]);
    expect(of(actions, "release")).toHaveLength(1);
    expect(engine.update([], 5000).cursors).toEqual([]);
  });

  it("reports one cursor per hand with its pose", () => {
    const engine = new GestureEngine();
    run(engine, repeat(3, () => [synthHand(0.4, 0.4, "pinch"), synthHand(0.6, 0.6, "open")]));
    const { cursors } = engine.update([synthHand(0.4, 0.4, "pinch"), synthHand(0.6, 0.6, "open")], 2000);
    expect(cursors.map((c) => c.pose).sort()).toEqual(["palm", "pinch"]);
  });
});

describe("ghost hands", () => {
  it("drops a second detection sitting on the same palm, keeps two real hands", async () => {
    const { dedupeHands } = await import("./gestures");
    const hand = synthHand(0.4, 0.5, "point");
    const ghost = synthHand(0.41, 0.505, "point");
    expect(dedupeHands([hand, ghost])).toEqual([hand]);
    const other = synthHand(0.7, 0.5, "open");
    expect(dedupeHands([hand, other])).toHaveLength(2);
  });
});
