import { describe, expect, it } from "vitest";

import {
  GestureEngine,
  HOLD_MS,
  PEACE_MS,
  PINCH_IN,
  PINCH_OUT,
  SWIPE_REVERSE_MS,
  THUMBS_MS,
  TWO_PALM_MS,
  dedupeHands,
  handMetrics,
  synthHand,
  toScreen,
  type GestureAction,
  type GestureFrame,
  type HandLandmarks,
  type SynthPose,
} from "./gestures";

const FRAME_MS = 33; // 30 fps, a typical webcam

/** Feed frames FRAME_MS apart and collect every action and frame. */
function run(engine: GestureEngine, frames: HandLandmarks[][], start = 1000) {
  const actions: GestureAction[] = [];
  const out: GestureFrame[] = [];
  frames.forEach((hands, i) => {
    const frame = engine.update(hands, start + i * FRAME_MS);
    out.push(frame);
    actions.push(...frame.actions);
  });
  return { actions, frames: out };
}

const repeat = <T,>(n: number, make: (i: number) => T): T[] => Array.from({ length: n }, (_, i) => make(i));
const framesFor = (ms: number) => Math.ceil(ms / FRAME_MS);
const of = (actions: GestureAction[], type: GestureAction["type"]) => actions.filter((a) => a.type === type);
const types = (actions: GestureAction[]) => [...new Set(actions.map((a) => a.type))].sort();
const hand = (x: number, y: number, pose: SynthPose, scale?: number) => synthHand(x, y, pose, scale);

/** A pinching hand whose thumb sits `ratio` palm sizes from the index tip, sideways. */
function pinchAt(x: number, y: number, ratio: number, scale = 0.09): HandLandmarks {
  const lm = synthHand(x, y, "pinch", scale).map((p) => ({ ...p }));
  const size = handMetrics(lm).size;
  lm[4] = { x: lm[8].x - ratio * size, y: lm[8].y };
  return lm;
}

/** The same hand seen with its palm tilted towards the camera (vertical foreshortening). */
function tilted(lm: HandLandmarks, k = 0.5): HandLandmarks {
  const wy = lm[0].y;
  return lm.map((p) => ({ x: p.x, y: wy + (p.y - wy) * k }));
}

const SIZES = [0.05, 0.09, 0.16] as const;

describe("hand metrics", () => {
  it.each(SIZES)("tells every pose apart at hand size %s", (scale) => {
    const m = (pose: SynthPose) => handMetrics(synthHand(0.5, 0.5, pose, scale));
    const flags = (pose: SynthPose) => {
      const x = m(pose);
      return {
        open: x.open,
        fist: x.fist,
        point: x.point,
        peace: x.peace,
        thumbsUp: x.thumbsUp,
        // The engine's rule: thumb on index, and not a fist (whose thumb lies on the curled index).
        pinch: x.pinch < PINCH_IN && !x.fist,
      };
    };
    const none = { open: false, fist: false, point: false, peace: false, thumbsUp: false, pinch: false };
    expect(flags("open")).toEqual({ ...none, open: true });
    expect(flags("fist")).toEqual({ ...none, fist: true });
    expect(flags("point")).toEqual({ ...none, point: true });
    expect(flags("peace")).toEqual({ ...none, peace: true });
    expect(flags("thumbs")).toEqual({ ...none, thumbsUp: true });
    // A pinch is an OK-sign: the thumb on the index, the other fingers free.
    expect(flags("pinch").pinch).toBe(true);
    expect(m("pinch").open).toBe(false);
  });

  it("normalises the pinch by the palm, so near and far hands read the same", () => {
    const ratios = SIZES.map((s) => handMetrics(pinchAt(0.5, 0.5, 0.25, s)).pinch);
    for (const r of ratios) expect(r).toBeCloseTo(0.25, 5);
    expect(handMetrics(synthHand(0.5, 0.5, "open", 0.05)).pinch).toBeCloseTo(handMetrics(synthHand(0.5, 0.5, "open", 0.16)).pinch, 5);
  });

  it("keeps a pinch on a palm tilted towards the camera", () => {
    const flat = pinchAt(0.5, 0.5, 0.25);
    const tilt = tilted(flat);
    // Over the foreshortened wrist-to-knuckle span alone this is 0.5, past PINCH_OUT.
    const m = handMetrics(tilt);
    expect(Math.hypot(tilt[4].x - tilt[8].x, tilt[4].y - tilt[8].y) / m.span).toBeGreaterThan(PINCH_OUT);
    expect(m.pinch).toBeLessThan(PINCH_OUT);
  });

  it("mirrors the camera and reaches the edges from a central zone", () => {
    expect(toScreen({ x: 0.5, y: 0.5 })).toEqual({ x: 0.5, y: 0.5 });
    // The hand on the camera's left is on the person's right.
    expect(toScreen({ x: 0.2, y: 0.5 }).x).toBeGreaterThan(0.5);
    expect(toScreen({ x: 0.0, y: 1.0 })).toEqual({ x: 1, y: 1 });
  });
});

describe("each pose held still does only its own thing", () => {
  const hold = (pose: SynthPose) => run(new GestureEngine(), repeat(framesFor(1600), () => [hand(0.4, 0.5, pose)])).actions;
  it.each<[SynthPose, string[]]>([
    ["point", ["hover"]],
    ["open", []],
    ["fist", []],
    ["peace", ["reset"]],
    ["thumbs", ["confirm"]],
    ["pinch", ["open"]],
  ])("%s → %j", (pose, expected) => {
    expect(types(hold(pose))).toEqual(expected);
  });

  it("fires a held gesture once, however long it is held", () => {
    expect(of(hold("peace"), "reset")).toHaveLength(1);
    expect(of(hold("thumbs"), "confirm")).toHaveLength(1);
    expect(of(hold("pinch"), "open")).toHaveLength(1);
  });
});

describe("pointing", () => {
  it("hovers at the fingertip once the hand has been seen a few frames", () => {
    const { actions } = run(new GestureEngine(), repeat(6, () => [hand(0.3, 0.5, "point")]));
    const hovers = of(actions, "hover");
    expect(hovers.length).toBe(4); // frames 3..6
    const last = hovers.at(-1);
    expect(last?.type === "hover" && last.point.x).toBeGreaterThan(0.6); // mirrored
  });
});

describe("pinch: tap selects, hold opens, move pans", () => {
  const point = (n: number, scale?: number) => repeat(n, () => [hand(0.4, 0.4, "point", scale)]);
  const pinch = (n: number, scale?: number) => repeat(n, () => [hand(0.4, 0.4, "pinch", scale)]);

  it("selects exactly where the cursor was hovering, not at the closed fingers", () => {
    const { actions, frames } = run(new GestureEngine(), [...point(5), ...pinch(6), ...point(3)]);
    const hover = of(frames[4].actions, "hover")[0]; // the last hover before the pinch
    const selects = of(actions, "select");
    expect(selects).toHaveLength(1);
    const sel = selects[0];
    if (sel.type !== "select" || hover?.type !== "hover") throw new Error("expected a select after a hover");
    expect(Math.hypot(sel.point.x - hover.point.x, sel.point.y - hover.point.y)).toBeLessThan(0.01);
    // The thumb-index midpoint the old engine selected at is far off the hovered node.
    const mid = toScreen(handMetrics(hand(0.4, 0.4, "pinch")).pinchAt);
    expect(Math.hypot(mid.x - hover.point.x, mid.y - hover.point.y)).toBeGreaterThan(0.05);
  });

  it("takes an unhurried pinch (half a second) as a tap", () => {
    const { actions } = run(new GestureEngine(), [...point(4), ...pinch(framesFor(500)), ...point(3)]);
    expect(of(actions, "select")).toHaveLength(1);
    expect(of(actions, "open")).toHaveLength(0);
  });

  it("opens on a still pinch held HOLD_MS, once, and does not also select", () => {
    const { actions, frames } = run(new GestureEngine(), [...point(4), ...pinch(framesFor(HOLD_MS) + 10), ...point(3)]);
    expect(of(actions, "open")).toHaveLength(1);
    expect(of(actions, "select")).toHaveLength(0);
    const charging = frames.filter((f) => f.gesture?.id === "pinch").map((f) => f.gesture?.progress ?? 0);
    expect(charging.length).toBeGreaterThan(5);
    expect(charging).toEqual([...charging].sort((a, b) => a - b)); // fills steadily
    expect(frames.some((f) => f.gesture?.id === "open")).toBe(true);
    const ring = frames.flatMap((f) => f.cursors).filter((c) => c.pose === "pinch" && c.progress !== undefined);
    expect(ring.length).toBeGreaterThan(5);
  });

  it.each(SIZES)("taps and holds the same with a hand of size %s", (scale) => {
    const tap = run(new GestureEngine(), [...point(4, scale), ...pinch(6, scale), ...point(3, scale)]).actions;
    expect(of(tap, "select")).toHaveLength(1);
    const held = run(new GestureEngine(), [...point(4, scale), ...pinch(framesFor(HOLD_MS) + 4, scale)]).actions;
    expect(of(held, "open")).toHaveLength(1);
  });

  it("ignores a one-frame near-pinch (it must be earned)", () => {
    const { actions } = run(new GestureEngine(), [...point(3), [hand(0.4, 0.4, "pinch")], ...point(3)]);
    expect(of(actions, "select")).toHaveLength(0);
  });

  it("does not flicker on and off when the fingers hover around the threshold", () => {
    // Before: alternating just under and just over PINCH_IN never earns a pinch.
    const before = repeat(20, (i) => [pinchAt(0.4, 0.4, i % 2 ? 0.28 : 0.34)]);
    expect(of(run(new GestureEngine(), [...point(3), ...before]).actions, "select")).toHaveLength(0);
    // During: single noisy frames past PINCH_OUT do not let go, so one tap is one select.
    const during = [0.2, 0.22, 0.2, 0.47, 0.2, 0.38, 0.47, 0.25, 0.2].map((r) => [pinchAt(0.4, 0.4, r)]);
    const { actions } = run(new GestureEngine(), [...point(4), ...during, ...point(4)]);
    expect(of(actions, "select")).toHaveLength(1);
    // And a noisy hold still opens exactly once.
    const noisyHold = repeat(framesFor(HOLD_MS) + 6, (i) => [pinchAt(0.4, 0.4, i % 5 === 2 ? 0.46 : 0.22)]);
    const held = run(new GestureEngine(), [...point(4), ...noisyHold, ...point(3)]).actions;
    expect(of(held, "open")).toHaveLength(1);
    expect(of(held, "select")).toHaveLength(0);
  });

  it("keeps a held pinch while the palm tilts towards the camera", () => {
    const frames = repeat(framesFor(HOLD_MS) + 4, (i) => [i < 4 ? pinchAt(0.4, 0.4, 0.2) : tilted(pinchAt(0.4, 0.4, 0.25))]);
    const { actions } = run(new GestureEngine(), [...point(4), ...frames]);
    expect(of(actions, "open")).toHaveLength(1);
  });

  it("pans with a moving pinch, then lets go, and neither selects nor opens", () => {
    const { actions } = run(new GestureEngine(), [
      ...point(3),
      ...repeat(12, (i) => [hand(0.4 + i * 0.015, 0.4, "pinch")]),
      ...repeat(2, () => [hand(0.565, 0.4, "point")]),
    ]);
    const drags = of(actions, "drag");
    expect(drags.length).toBeGreaterThan(5);
    for (const d of drags) expect(d.type === "drag" && d.dx).toBeLessThan(0); // mirrored
    expect(of(actions, "release")).toHaveLength(1);
    expect(of(actions, "select")).toHaveLength(0);
    expect(of(actions, "open")).toHaveLength(0);
  });

  it("does not take a fist for a pinch", () => {
    const { actions, frames } = run(new GestureEngine(), [...point(3), ...repeat(8, () => [hand(0.4, 0.4, "fist")]), ...point(3)]);
    expect(of(actions, "select")).toHaveLength(0);
    expect(frames.flatMap((f) => f.cursors).some((c) => c.pose === "pinch")).toBe(false);
  });
});

describe("fist: grab and move the map", () => {
  it("drags with the hand while closed and lets go when it opens", () => {
    const { actions } = run(new GestureEngine(), [
      ...repeat(10, (i) => [hand(0.4 + i * 0.02, 0.5, "fist")]),
      ...repeat(2, () => [hand(0.58, 0.5, "open")]),
    ]);
    const drags = of(actions, "drag");
    expect(drags.length).toBeGreaterThanOrEqual(6);
    for (const d of drags) expect(d.type === "drag" && d.dx).toBeLessThan(0);
    expect(of(actions, "release")).toHaveLength(1);
  });

  it("a still fist does not drag", () => {
    expect(of(run(new GestureEngine(), repeat(10, () => [hand(0.4, 0.5, "fist")])).actions, "drag")).toHaveLength(0);
  });
});

describe("two hands pinching: zoom", () => {
  it("zooms in apart, out together, and selects nothing", () => {
    const apart = run(new GestureEngine(), [
      ...repeat(2, () => [hand(0.4, 0.5, "point"), hand(0.6, 0.5, "point")]),
      ...repeat(10, (i) => [hand(0.4 - i * 0.01, 0.5, "pinch"), hand(0.6 + i * 0.01, 0.5, "pinch")]),
      [hand(0.3, 0.5, "point"), hand(0.7, 0.5, "point")],
      [hand(0.3, 0.5, "point"), hand(0.7, 0.5, "point")],
    ]);
    const zooms = of(apart.actions, "zoom");
    expect(zooms.length).toBeGreaterThan(3);
    const total = zooms.reduce((acc, z) => acc * (z.type === "zoom" ? z.scale : 1), 1);
    expect(total).toBeGreaterThan(1.5);
    expect(of(apart.actions, "select")).toHaveLength(0);
    expect(of(apart.actions, "drag")).toHaveLength(0);
    expect(apart.frames.some((f) => f.gesture?.id === "zoom")).toBe(true);

    const together = run(new GestureEngine(), [
      ...repeat(2, () => [hand(0.3, 0.5, "point"), hand(0.7, 0.5, "point")]),
      ...repeat(10, (i) => [hand(0.3 + i * 0.01, 0.5, "pinch"), hand(0.7 - i * 0.01, 0.5, "pinch")]),
    ]);
    const shrink = of(together.actions, "zoom").reduce((acc, z) => acc * (z.type === "zoom" ? z.scale : 1), 1);
    expect(shrink).toBeLessThan(0.75);
  });
});

describe("open palm swipe: sections", () => {
  const swipe = (from: number, to: number, frames = 6) => repeat(frames, (i) => [hand(from + ((to - from) * i) / (frames - 1), 0.5, "open")]);

  it("a swipe to the person's right is next, to the left is previous", () => {
    // Camera x falls as the hand moves to the person's right (the image is mirrored).
    const right = run(new GestureEngine(), swipe(0.6, 0.4)).actions;
    expect(of(right, "section")).toEqual([{ type: "section", step: 1 }]);
    const left = run(new GestureEngine(), swipe(0.4, 0.6)).actions;
    expect(of(left, "section")).toEqual([{ type: "section", step: -1 }]);
  });

  it("a slow drift or a vertical wave is not a swipe", () => {
    expect(of(run(new GestureEngine(), swipe(0.6, 0.4, 40)).actions, "section")).toHaveLength(0);
    const vertical = repeat(6, (i) => [hand(0.5, 0.35 + i * 0.05, "open")]);
    expect(of(run(new GestureEngine(), vertical).actions, "section")).toHaveLength(0);
  });

  it("the hand coming back is not a swipe the other way", () => {
    const { actions } = run(new GestureEngine(), [...swipe(0.6, 0.4), ...swipe(0.4, 0.6)]);
    expect(of(actions, "section")).toEqual([{ type: "section", step: 1 }]);
    // Once the reverse window has passed, the other way works again.
    const later = new GestureEngine();
    run(later, swipe(0.6, 0.4), 1000);
    const back = run(later, swipe(0.4, 0.6), 1000 + SWIPE_REVERSE_MS + 400).actions;
    expect(of(back, "section")).toEqual([{ type: "section", step: -1 }]);
  });
});

describe("reset", () => {
  it("a peace sign held PEACE_MS resets the view once, filling as it is held", () => {
    const { actions, frames } = run(new GestureEngine(), repeat(framesFor(PEACE_MS) + 30, () => [hand(0.5, 0.5, "peace")]));
    expect(of(actions, "reset")).toHaveLength(1);
    const firstReset = frames.findIndex((f) => f.actions.some((a) => a.type === "reset"));
    expect(firstReset * FRAME_MS).toBeGreaterThanOrEqual(PEACE_MS);
    expect(frames[2].gesture).toMatchObject({ id: "reset" });
    expect(frames[2].gesture?.progress).toBeLessThan(1);
  });

  it("a peace sign flashed briefly does nothing", () => {
    const { actions } = run(new GestureEngine(), [...repeat(4, () => [hand(0.5, 0.5, "peace")]), ...repeat(4, () => [hand(0.5, 0.5, "point")])]);
    expect(of(actions, "reset")).toHaveLength(0);
  });

  it("both palms open and still for TWO_PALM_MS reset the view", () => {
    const { actions } = run(
      new GestureEngine(),
      repeat(framesFor(TWO_PALM_MS) + 4, () => [hand(0.3, 0.5, "open"), hand(0.7, 0.5, "open")]),
    );
    expect(of(actions, "reset")).toHaveLength(1);
  });

  it("two palms on the move do not reset", () => {
    const moving = repeat(framesFor(TWO_PALM_MS) + 4, (i) => [hand(0.25 + 0.006 * i, 0.5, "open"), hand(0.75 - 0.006 * i, 0.5, "open")]);
    expect(of(run(new GestureEngine(), moving).actions, "reset")).toHaveLength(0);
  });

  it("does not reset twice in a row when both reset poses follow each other", () => {
    const { actions } = run(new GestureEngine(), [
      ...repeat(framesFor(PEACE_MS) + 2, () => [hand(0.5, 0.5, "peace")]),
      ...repeat(framesFor(TWO_PALM_MS) + 2, () => [hand(0.3, 0.5, "open"), hand(0.7, 0.5, "open")]),
    ]);
    expect(of(actions, "reset")).toHaveLength(1);
  });
});

describe("thumbs up: confirm", () => {
  it("confirms once after THUMBS_MS, not on a passing thumb", () => {
    const held = run(new GestureEngine(), repeat(framesFor(THUMBS_MS) + 2, () => [hand(0.5, 0.5, "thumbs")]));
    expect(of(held.actions, "confirm")).toHaveLength(1);
    expect(held.frames[3].gesture).toMatchObject({ id: "confirm" });
    const passing = run(new GestureEngine(), [...repeat(4, () => [hand(0.5, 0.5, "thumbs")]), ...repeat(3, () => [hand(0.5, 0.5, "fist")])]);
    expect(of(passing.actions, "confirm")).toHaveLength(0);
  });
});

describe("hand identity and loss", () => {
  it("keeps each hand's state when MediaPipe swaps their order", () => {
    const left = (pose: SynthPose) => hand(0.3, 0.5, pose);
    const right = () => hand(0.7, 0.5, "point");
    const { actions } = run(new GestureEngine(), [
      ...repeat(3, () => [left("point"), right()]),
      [left("pinch"), right()],
      [right(), left("pinch")], // order swapped: still the same pinching hand
      [left("pinch"), right()],
      [right(), left("point")],
      [left("point"), right()],
    ]);
    expect(of(actions, "select")).toHaveLength(1);
  });

  it("losing a hand mid-pan releases, and leaves no cursor", () => {
    const engine = new GestureEngine();
    const { actions } = run(engine, [...repeat(8, (i) => [hand(0.4 + i * 0.02, 0.4, "fist")]), []]);
    expect(of(actions, "release")).toHaveLength(1);
    expect(engine.update([], 5000).cursors).toEqual([]);
  });

  it("reports one cursor per hand with its pose, and what it sees", () => {
    const engine = new GestureEngine();
    run(engine, repeat(3, () => [hand(0.4, 0.4, "pinch"), hand(0.6, 0.6, "thumbs")]));
    const frame = engine.update([hand(0.4, 0.4, "pinch"), hand(0.6, 0.6, "thumbs")], 2000);
    expect(frame.cursors.map((c) => c.pose).sort()).toEqual(["pinch", "thumbs"]);
    expect(engine.update([], 2100).gesture).toBeNull();
  });
});

describe("ghost hands", () => {
  it("drops a second detection sitting on the same palm, keeps two real hands", () => {
    const one = hand(0.4, 0.5, "point");
    const ghost = hand(0.41, 0.505, "point");
    expect(dedupeHands([one, ghost])).toEqual([one]);
    const other = hand(0.7, 0.5, "open");
    expect(dedupeHands([one, other])).toHaveLength(2);
  });
});
