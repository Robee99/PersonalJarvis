/**
 * Hand landmarks → Orb actions. Pure: frames in, actions out, no camera, no
 * DOM, so every gesture is unit-tested with synthetic hands.
 *
 * The hand model is the one the Hands view (holo/holo.html, MIT) was tuned on
 * with a real camera: MediaPipe's 21 landmarks, finger extension measured as
 * tip-to-wrist distance over the wrist-to-middle-knuckle span, a pinch as
 * thumb tip to index tip under that span with hysteresis (in below
 * `PINCH_IN`, out above `PINCH_OUT`) that must hold `PINCH_EARN` frames, the
 * index fingertip as the cursor, the image mirrored so the hand moves the way
 * the person sees it, and a reach gain so a comfortable central zone covers
 * the whole map. The thresholds are copied, not re-tuned.
 *
 * Gestures:
 *   point (any hand, not pinching)  → hover at the fingertip
 *   pinch and let go quickly, still → select at the pinch (again = open)
 *   open palm, moved                → drag (the Orb pans/orbits the map)
 *   both hands pinching, apart/in   → zoom by the change in their distance
 */

export interface Landmark {
  x: number;
  y: number;
  z?: number;
}

export type HandLandmarks = Landmark[];

/** A point on the Orb, 0..1 on both axes, already mirrored. */
export interface ScreenPoint {
  x: number;
  y: number;
}

export type HandPose = "point" | "pinch" | "palm" | "other";

export interface HandCursor extends ScreenPoint {
  slot: number;
  pose: HandPose;
}

export type GestureAction =
  | { type: "hover"; point: ScreenPoint }
  | { type: "select"; point: ScreenPoint }
  | { type: "drag"; dx: number; dy: number }
  | { type: "zoom"; scale: number }
  | { type: "release" };

export interface GestureFrame {
  actions: GestureAction[];
  cursors: HandCursor[];
}

export const PINCH_IN = 0.3;
export const PINCH_OUT = 0.42;
export const PINCH_EARN = 2;
/** Reach gain around the centre of the camera image. */
export const REACH = 1.45;
/** A pinch released within this long, having moved less than TAP_SLOP, is a tap. */
export const TAP_MS = 450;
export const TAP_SLOP = 0.05;
/** Frames a hand must be seen before it may hover (kills one-frame ghosts). */
export const HOVER_EARN = 3;
/** Palm movement below this (per frame, 0..1 units) is tremor, not a drag. */
export const DRAG_DEADZONE = 0.002;

const dist = (a: Landmark, b: Landmark) => Math.hypot(a.x - b.x, a.y - b.y);

export interface HandMetrics {
  span: number;
  pinch: number;
  open: boolean;
  fist: boolean;
  point: boolean;
  palm: Landmark;
  tip: Landmark;
  pinchAt: Landmark;
}

export function handMetrics(lm: HandLandmarks): HandMetrics {
  const span = Math.max(1e-4, dist(lm[0], lm[9]));
  const ext = (i: number) => dist(lm[i], lm[0]) / span;
  const tips = [ext(8), ext(12), ext(16), ext(20)];
  const pinch = dist(lm[4], lm[8]) / span;
  return {
    span,
    pinch,
    // "openish" in the Hands view (the relaxed real-hand open palm), with the
    // thumb away from the index so a pinching hand is never also a palm.
    open: tips.every((t) => t > 1.32) && pinch > PINCH_OUT,
    fist: tips.every((t) => t < 1.05),
    point: tips[0] > 1.45 && tips[1] < 1.15 && tips[2] < 1.15 && tips[3] < 1.15,
    palm: { x: (lm[0].x + lm[9].x) / 2, y: (lm[0].y + lm[9].y) / 2 },
    tip: lm[8],
    pinchAt: { x: (lm[4].x + lm[8].x) / 2, y: (lm[4].y + lm[8].y) / 2 },
  };
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

/** Camera-normalised → Orb-normalised: mirrored, with the reach gain. */
export function toScreen(p: Landmark, reach = REACH): ScreenPoint {
  return {
    x: clamp01(0.5 + (1 - p.x - 0.5) * reach),
    y: clamp01(0.5 + (p.y - 0.5) * reach),
  };
}

/**
 * One Euro filter (Casiez et al.), as in the Hands view: heavy smoothing when
 * the hand is still (no jitter), light when it moves fast (no lag).
 */
class OneEuro {
  private t = 0;
  private x = 0;
  private y = 0;
  private dx = 0;
  private dy = 0;
  private primed = false;

  filter(p: ScreenPoint, now: number): ScreenPoint {
    if (!this.primed) {
      this.primed = true;
      this.t = now;
      this.x = p.x;
      this.y = p.y;
      return { x: p.x, y: p.y };
    }
    let dt = (now - this.t) / 1000;
    if (!(dt > 1 / 240)) dt = 1 / 60;
    if (dt > 0.2) dt = 0.2;
    this.t = now;
    const alpha = (cutoff: number) => {
      const r = 2 * Math.PI * cutoff * dt;
      return r / (r + 1);
    };
    // The Hands view works in pixels; these are 0..1 units, hence the scale.
    const ad = alpha(2.5);
    this.dx += ((p.x - this.x) / dt - this.dx) * ad;
    this.dy += ((p.y - this.y) / dt - this.dy) * ad;
    const a = alpha(1.3 + 9 * Math.hypot(this.dx, this.dy));
    this.x += (p.x - this.x) * a;
    this.y += (p.y - this.y) * a;
    return { x: this.x, y: this.y };
  }
}

interface HandState {
  present: boolean;
  seen: number;
  pinch: boolean;
  pinchFrames: number;
  pinchT: number;
  pinchStart: ScreenPoint | null;
  pinchTravel: number;
  palmFrames: number;
  lastPalm: ScreenPoint | null;
  lastPalmRaw: Landmark | null;
  smooth: OneEuro;
}

const newHand = (): HandState => ({
  present: false,
  seen: 0,
  pinch: false,
  pinchFrames: 0,
  pinchT: 0,
  pinchStart: null,
  pinchTravel: 0,
  palmFrames: 0,
  lastPalm: null,
  lastPalmRaw: null,
  smooth: new OneEuro(),
});

/** Slot hands by nearest palm so MediaPipe's arbitrary order cannot swap state. */
function assign(hands: HandLandmarks[], states: HandState[]): (HandLandmarks | null)[] {
  const out: (HandLandmarks | null)[] = [null, null];
  const taken = new Set<number>();
  const palmOf = (lm: HandLandmarks) => ({ x: (lm[0].x + lm[9].x) / 2, y: (lm[0].y + lm[9].y) / 2 });
  const candidates = hands.slice(0, 2);
  const order = [0, 1].filter((i) => states[i].present && states[i].lastPalmRaw).sort((a, b) => states[b].seen - states[a].seen);
  for (const i of order) {
    const last = states[i].lastPalmRaw as Landmark;
    let best = -1;
    let bestD = Infinity;
    candidates.forEach((lm, k) => {
      if (taken.has(k)) return;
      const d = dist(palmOf(lm), last);
      if (d < bestD) {
        bestD = d;
        best = k;
      }
    });
    if (best >= 0 && bestD < 0.25) {
      out[i] = candidates[best];
      taken.add(best);
    }
  }
  candidates.forEach((lm, k) => {
    if (taken.has(k)) return;
    const slot = out[0] === null && !states[0].present ? 0 : out[1] === null && !states[1].present ? 1 : out[0] === null ? 0 : out[1] === null ? 1 : -1;
    if (slot >= 0) {
      out[slot] = lm;
      taken.add(k);
    }
  });
  return out;
}

export class GestureEngine {
  private hands: HandState[] = [newHand(), newHand()];
  private zoomFrom: number | null = null;
  private zoomed = false;

  reset(): void {
    this.hands = [newHand(), newHand()];
    this.zoomFrom = null;
    this.zoomed = false;
  }

  /** One camera frame. `now` is in milliseconds (performance.now()). */
  update(landmarks: HandLandmarks[], now: number): GestureFrame {
    const actions: GestureAction[] = [];
    const cursors: HandCursor[] = [];
    const slots = assign(landmarks.filter((lm) => lm && lm.length >= 21), this.hands);
    const metrics: (HandMetrics | null)[] = [null, null];

    slots.forEach((lm, i) => {
      const st = this.hands[i];
      if (!lm) {
        if (st.present) {
          if (st.pinch || st.palmFrames) actions.push({ type: "release" });
          this.hands[i] = newHand();
        }
        return;
      }
      const m = handMetrics(lm);
      metrics[i] = m;
      st.present = true;
      st.seen += 1;
      st.lastPalmRaw = m.palm;
    });

    const both = metrics[0] !== null && metrics[1] !== null;

    metrics.forEach((m, i) => {
      if (!m) return;
      const st = this.hands[i];
      const cursor = st.smooth.filter(toScreen(m.tip), now);

      // Pinch with hysteresis, earned over PINCH_EARN frames.
      if (!st.pinch) {
        if (m.pinch < PINCH_IN && st.seen >= 2) {
          st.pinchFrames += 1;
          if (st.pinchFrames >= PINCH_EARN) {
            st.pinch = true;
            st.pinchT = now;
            st.pinchStart = toScreen(m.pinchAt);
            st.pinchTravel = 0;
          }
        } else st.pinchFrames = 0;
      } else if (m.pinch > PINCH_OUT) {
        const quick = now - st.pinchT < TAP_MS && st.pinchTravel < TAP_SLOP;
        // A tap that was half of a two-hand zoom is not a selection.
        if (quick && !this.zoomed && st.pinchStart) actions.push({ type: "select", point: st.pinchStart });
        st.pinch = false;
        st.pinchFrames = 0;
        st.pinchStart = null;
        if (!this.hands.some((h) => h.pinch)) this.zoomed = false;
      } else if (st.pinchStart) {
        const at = toScreen(m.pinchAt);
        st.pinchTravel = Math.max(st.pinchTravel, Math.hypot(at.x - st.pinchStart.x, at.y - st.pinchStart.y));
      }

      // Open palm drags; only one hand at a time, and never while zooming.
      const palm = toScreen(m.palm);
      if (m.open && !st.pinch && !both) {
        st.palmFrames += 1;
        if (st.palmFrames >= 2 && st.lastPalm) {
          const dx = palm.x - st.lastPalm.x;
          const dy = palm.y - st.lastPalm.y;
          if (Math.hypot(dx, dy) > DRAG_DEADZONE) actions.push({ type: "drag", dx, dy });
        }
        st.lastPalm = palm;
      } else {
        if (st.palmFrames >= 2) actions.push({ type: "release" });
        st.palmFrames = 0;
        st.lastPalm = null;
      }

      const pose: HandPose = st.pinch ? "pinch" : m.open ? "palm" : m.point ? "point" : "other";
      cursors.push({ slot: i, pose, ...cursor });
    });

    // Two pinching hands zoom by the change in the distance between them.
    const [a, b] = this.hands;
    if (both && a.pinch && b.pinch && metrics[0] && metrics[1]) {
      const d = dist(toScreen(metrics[0].pinchAt), toScreen(metrics[1].pinchAt));
      if (this.zoomFrom !== null && this.zoomFrom > 1e-3 && d > 1e-3) {
        const scale = d / this.zoomFrom;
        if (Math.abs(scale - 1) > 0.004) actions.push({ type: "zoom", scale });
      }
      this.zoomFrom = d;
      this.zoomed = true;
    } else this.zoomFrom = null;

    // Hover follows the first hand that is pointing (or merely present) and
    // not busy pinching, dragging or zooming.
    if (!this.zoomed) {
      const hover = cursors.find((c) => c.pose !== "pinch" && c.pose !== "palm" && this.hands[c.slot].seen >= HOVER_EARN);
      if (hover) actions.push({ type: "hover", point: { x: hover.x, y: hover.y } });
    }
    return { actions, cursors };
  }
}

/**
 * A plausible right hand at (x, y) in camera coordinates, in one pose. Ported
 * from the Hands view's own simulator (holo/holo.html `synthHand`, MIT) so the
 * tests feed the engine the same shapes that page was verified against.
 */
export function synthHand(x: number, y: number, pose: "open" | "pinch" | "point" | "fist", scale = 0.09): HandLandmarks {
  const s = scale;
  const lm: Landmark[] = [];
  const spread = pose === "open" ? 1.0 : pose === "fist" ? 0.38 : 0.78;
  lm[0] = { x, y: y + s * 0.9 };
  lm[9] = { x, y: y - s * 0.1 };
  lm[1] = { x: x - s * 0.35, y: y + s * 0.55 };
  lm[2] = { x: x - s * 0.5, y: y + s * 0.3 };
  lm[3] = { x: x - s * 0.55, y: y + s * 0.1 };
  lm[5] = { x: x - s * 0.3, y: y - s * 0.15 };
  lm[13] = { x: x + s * 0.15, y: y - s * 0.15 };
  lm[17] = { x: x + s * 0.32, y: y - s * 0.1 };
  const tipY = y - s * (pose === "fist" ? 0.35 : 1.05) * spread;
  lm[8] = {
    x: pose === "pinch" ? x - s * 0.18 : x - s * 0.28 * (pose === "point" ? 1 : spread),
    y: pose === "pinch" ? y - s * 0.5 : pose === "point" ? y - s * 1.05 : tipY,
  };
  lm[4] = pose === "pinch" ? { x: x - s * 0.16, y: y - s * 0.48 } : { x: x - s * 0.62 * spread, y: y + s * 0.05 };
  const curlY = y - s * 0.133;
  lm[12] = { x: x - s * 0.05, y: pose === "point" ? curlY : tipY };
  lm[16] = { x: x + s * 0.16, y: (pose === "point" ? curlY : tipY) + s * 0.06 };
  lm[20] = { x: x + s * 0.3, y: (pose === "point" ? curlY : tipY) + s * 0.14 };
  for (const [p0, q0, i] of [
    [5, 8, 6],
    [5, 8, 7],
    [9, 12, 10],
    [9, 12, 11],
    [13, 16, 14],
    [13, 16, 15],
    [17, 20, 18],
    [17, 20, 19],
  ]) {
    const p = lm[p0];
    const q = lm[q0];
    const k = i % 2 ? 0.66 : 0.33;
    lm[i] = { x: p.x + (q.x - p.x) * k, y: p.y + (q.y - p.y) * k };
  }
  return lm;
}

/**
 * Drop ghost hands: MediaPipe often reports one physical hand twice, and the
 * ghost would paint a second cursor shadowing the finger. Two palms closer
 * than ~1.35 hand spans are the same hand; the first (higher-scored, when the
 * caller sorted by score) is kept. Same rule as the Hands view.
 */
export function dedupeHands(hands: HandLandmarks[]): HandLandmarks[] {
  const kept: HandLandmarks[] = [];
  for (const lm of hands) {
    if (!lm || lm.length < 21) continue;
    const m = handMetrics(lm);
    const twin = kept.some((other) => {
      const o = handMetrics(other);
      return dist(m.palm, o.palm) < 1.35 * Math.max(m.span, o.span);
    });
    if (!twin) kept.push(lm);
  }
  return kept.slice(0, 2);
}
