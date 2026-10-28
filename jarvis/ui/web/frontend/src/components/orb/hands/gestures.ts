/**
 * Hand landmarks → Orb actions. Pure: frames in, actions out, no camera, no
 * DOM, so every gesture is unit-tested with synthetic hands.
 *
 * The hand model is the one the Hands view (holo/holo.html, MIT) was tuned on
 * with a real camera: MediaPipe's 21 landmarks, finger extension measured as
 * tip-to-wrist distance over the wrist-to-middle-knuckle span, a pinch as
 * thumb tip to index tip over the palm size with hysteresis (in below
 * `PINCH_IN` for `PINCH_EARN` frames, out above `PINCH_OUT` for `PINCH_DROP`
 * frames), the index fingertip as the cursor, the image mirrored so the hand
 * moves the way the person sees it, and a reach gain so a comfortable central
 * zone covers the whole map. The pose thresholds are copied, not re-tuned.
 *
 * Gestures, each a distinct pose so none can be read as another:
 *   point (index finger only)          → hover: the cursor, snapping to a node
 *   pinch and let go                   → select what the cursor was on
 *                                        (on the selected node: open it;
 *                                        on empty space: clear the selection)
 *   pinch and hold still (HOLD_MS)     → open what the cursor was on
 *   pinch and move, or fist and move   → pan the map with the hand
 *   both hands pinching, apart/in      → zoom by the change in their distance
 *   thumbs up, held (THUMBS_MS)        → confirm: open the selected node
 *   open palm, swiped left or right    → previous / next section
 *   peace sign held (PEACE_MS), or both
 *   palms open and still (TWO_PALM_MS) → reset the view
 *
 * And from the Hands view itself (Zubair Trabzada's holo page, MIT), the
 * gestures the Orb did not have yet:
 *   both hands pinching, twisted          → rotate (the page's "twist = rotate")
 *   a pan let go while moving fast        → flick: the map keeps gliding
 *   open palm closed to a fist quickly    → pull: fly in to what the cursor is on
 *   one open palm held still (PUSH_MS)    → push: the map moves back
 *
 * A pinch aims where the cursor was just before the fingers closed (the
 * index tip travels to the thumb while pinching, so the fingertip under the
 * pinch is not where the person was pointing). That is what makes "pinch the
 * highlighted node" select the highlighted node.
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

export type HandPose = "point" | "pinch" | "palm" | "fist" | "peace" | "thumbs" | "other";

export interface HandCursor extends ScreenPoint {
  slot: number;
  pose: HandPose;
  /** 0..1 while a held gesture (pinch-hold, peace, thumbs up) is charging. */
  progress?: number;
}

export type GestureAction =
  | { type: "hover"; point: ScreenPoint }
  | { type: "select"; point: ScreenPoint }
  | { type: "open"; point: ScreenPoint }
  | { type: "confirm" }
  | { type: "drag"; dx: number; dy: number }
  | { type: "zoom"; scale: number }
  | { type: "section"; step: 1 | -1 }
  | { type: "reset" }
  | { type: "release" }
  /** Radians the two pinching hands turned since the last frame. */
  | { type: "rotate"; angle: number }
  /** A pan let go at this speed (0..1 units per second). */
  | { type: "fling"; vx: number; vy: number }
  | { type: "pull"; point: ScreenPoint }
  | { type: "push" };

/** The gestures the guide lists, one row each. */
export const GESTURES = [
  "hover",
  "select",
  "open",
  "confirm",
  "pan",
  "flick",
  "zoom",
  "rotate",
  "pull",
  "push",
  "swipe",
  "reset",
] as const;
export type GestureId = (typeof GESTURES)[number];

/** What the engine sees right now; "pinch" is a pinch not yet a tap, hold or pan. */
export interface LiveGesture {
  id: GestureId | "pinch";
  /** 0..1 for gestures that fire after a hold. */
  progress?: number;
}

export interface GestureFrame {
  actions: GestureAction[];
  cursors: HandCursor[];
  gesture: LiveGesture | null;
}

export const PINCH_IN = 0.3;
export const PINCH_OUT = 0.42;
/** Frames below PINCH_IN before a pinch counts. */
export const PINCH_EARN = 2;
/** Frames above PINCH_OUT before a pinch ends: one noisy frame never lets go. */
export const PINCH_DROP = 2;
/** Above this the fingers are apart; the cursor here is what a pinch aims at. */
export const PINCH_ARM = 0.55;
/** How far back a pinch may reach for the cursor it aims at. */
export const AIM_LOOKBACK_MS = 400;
/** Reach gain around the centre of the camera image. */
export const REACH = 1.45;
/** A still pinch held this long opens; released earlier it is a tap. */
export const HOLD_MS = 650;
export const TAP_MS = HOLD_MS;
/** Palm travel (0..1 units) that turns a pinch into a pan instead of a tap or hold. */
export const TAP_SLOP = 0.04;
/** Frames a hand must be seen before it may hover (kills one-frame ghosts). */
export const HOVER_EARN = 3;
/** Movement below this (per frame, 0..1 units) is tremor, not a drag. */
export const DRAG_DEADZONE = 0.002;
/** Frames of fist before it grabs the map. */
export const FIST_EARN = 3;
export const PEACE_MS = 400;
export const THUMBS_MS = 450;
export const TWO_PALM_MS = 900;
/** Palm movement that restarts the two-palm hold. */
export const STILL_SLOP = 0.06;
export const RESET_COOLDOWN_MS = 1500;
/** A swipe is SWIPE_DIST of horizontal palm travel within SWIPE_MS. */
export const SWIPE_MS = 350;
export const SWIPE_DIST = 0.22;
export const SWIPE_COOLDOWN_MS = 700;
/** The hand coming back after a swipe is not a swipe the other way. */
export const SWIPE_REVERSE_MS = 1100;
/** Turning below this (radians per frame) between two pinches is tremor. */
export const TWIST_DEADZONE = 0.008;
/** A pan let go faster than this (0..1 units per second) keeps gliding. */
export const FLICK_SPEED = 1.1;
/** The stretch of a pan its release speed is measured over. */
export const FLICK_WINDOW_MS = 120;
/** An open palm closed to a fist within this pulls (the Hands view's 320 ms). */
export const PULL_MS = 320;
/** The palm must have been shown this long first, so closing a hand to grab is not a pull. */
export const PULL_SHOWN_MS = 250;
export const PULL_COOLDOWN_MS = 900;
/** One open palm held still this long pushes the map back (the page's repulsor, ~0.8 s). */
export const PUSH_MS = 800;
export const PUSH_COOLDOWN_MS = 1200;

const dist = (a: Landmark, b: Landmark) => Math.hypot(a.x - b.x, a.y - b.y);

export interface HandMetrics {
  /** Wrist to middle knuckle: the unit finger extension is measured in. */
  span: number;
  /** Palm size for the pinch: the span, or the knuckle width when the palm is tilted. */
  size: number;
  pinch: number;
  tips: number[];
  open: boolean;
  fist: boolean;
  point: boolean;
  peace: boolean;
  thumbsUp: boolean;
  palm: Landmark;
  tip: Landmark;
  pinchAt: Landmark;
}

export function handMetrics(lm: HandLandmarks): HandMetrics {
  const span = Math.max(1e-4, dist(lm[0], lm[9]));
  // A palm tilted towards the camera (common while reaching to pinch)
  // foreshortens the wrist-to-knuckle span but not the knuckle width; without
  // this floor the pinch ratio swells and a held pinch lets go by itself.
  const size = Math.max(span, dist(lm[5], lm[17]) / 0.8);
  const ext = (i: number) => dist(lm[i], lm[0]) / span;
  const tips = [ext(8), ext(12), ext(16), ext(20)];
  const pinch = dist(lm[4], lm[8]) / size;
  const curled = tips.every((t) => t < 1.1);
  const thumbsUp =
    curled && dist(lm[4], lm[5]) / span > 0.55 && lm[4].y < lm[5].y - 0.2 * span && lm[4].y < lm[3].y;
  return {
    span,
    size,
    pinch,
    tips,
    // "openish" in the Hands view (the relaxed real-hand open palm), with the
    // thumb away from the index so a pinching hand is never also a palm.
    open: tips.every((t) => t > 1.32) && pinch > PINCH_OUT,
    fist: tips.every((t) => t < 1.05) && !thumbsUp,
    point: tips[0] > 1.45 && tips[1] < 1.15 && tips[2] < 1.15 && tips[3] < 1.15,
    peace: tips[0] > 1.45 && tips[1] > 1.45 && tips[2] < 1.15 && tips[3] < 1.15 && pinch > PINCH_OUT,
    thumbsUp,
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
  smooth: OneEuro;
  lastPalmRaw: Landmark | null;
  /** The cursor the last time the fingers were apart. */
  arm: { point: ScreenPoint; t: number } | null;
  pinch: boolean;
  inFrames: number;
  outFrames: number;
  pinchT: number;
  aim: ScreenPoint | null;
  pinchPalm: ScreenPoint | null;
  lastPinchPalm: ScreenPoint | null;
  panning: boolean;
  held: boolean;
  fistFrames: number;
  grabbing: boolean;
  lastFistPalm: ScreenPoint | null;
  peaceSince: number | null;
  peaceFired: boolean;
  thumbSince: number | null;
  thumbFired: boolean;
  trail: { x: number; y: number; t: number }[];
  /** Palm positions while panning, for the speed it is let go at. */
  moves: { x: number; y: number; t: number }[];
  /** When the palm was last open, and where the cursor was. */
  openAt: { t: number; since: number; point: ScreenPoint } | null;
  pushSince: number | null;
  pushAnchor: ScreenPoint | null;
  pushFired: boolean;
}

const newHand = (): HandState => ({
  present: false,
  seen: 0,
  smooth: new OneEuro(),
  lastPalmRaw: null,
  arm: null,
  pinch: false,
  inFrames: 0,
  outFrames: 0,
  pinchT: 0,
  aim: null,
  pinchPalm: null,
  lastPinchPalm: null,
  panning: false,
  held: false,
  fistFrames: 0,
  grabbing: false,
  lastFistPalm: null,
  peaceSince: null,
  peaceFired: false,
  thumbSince: null,
  thumbFired: false,
  trail: [],
  moves: [],
  openAt: null,
  pushSince: null,
  pushAnchor: null,
  pushFired: false,
});

/** The fling a pan ends with, when the hand was still moving fast. */
function flingOf(moves: { x: number; y: number; t: number }[], now: number): GestureAction | null {
  const recent = moves.filter((p) => now - p.t <= FLICK_WINDOW_MS);
  if (recent.length < 2) return null;
  const first = recent[0];
  const last = recent[recent.length - 1];
  const dt = (last.t - first.t) / 1000;
  if (dt <= 0) return null;
  const vx = (last.x - first.x) / dt;
  const vy = (last.y - first.y) / dt;
  return Math.hypot(vx, vy) >= FLICK_SPEED ? { type: "fling", vx, vy } : null;
}

/** The smallest turn from one angle to another, in (-pi, pi]. */
function turn(from: number, to: number): number {
  let d = to - from;
  while (d > Math.PI) d -= 2 * Math.PI;
  while (d <= -Math.PI) d += 2 * Math.PI;
  return d;
}

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

const clampProgress = (v: number) => Math.min(1, Math.max(0, v));

export class GestureEngine {
  private hands: HandState[] = [newHand(), newHand()];
  private zoomFrom: number | null = null;
  private zoomed = false;
  private lastReset = -Infinity;
  private lastSwipe: { t: number; step: 1 | -1 } | null = null;
  private twoPalm: { since: number; anchor: ScreenPoint[]; fired: boolean } | null = null;
  private twistFrom: number | null = null;
  private lastPull = -Infinity;
  private lastPush = -Infinity;

  reset(): void {
    this.hands = [newHand(), newHand()];
    this.twistFrom = null;
    this.lastPull = -Infinity;
    this.lastPush = -Infinity;
    this.zoomFrom = null;
    this.zoomed = false;
    this.lastReset = -Infinity;
    this.lastSwipe = null;
    this.twoPalm = null;
  }

  /** Both reset poses share one cooldown, so doing both at once resets once. */
  private fireReset(actions: GestureAction[], now: number): void {
    if (now - this.lastReset < RESET_COOLDOWN_MS) return;
    this.lastReset = now;
    actions.push({ type: "reset" });
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
          if (st.panning || st.grabbing) actions.push({ type: "release" });
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
    const live: LiveGesture[] = [];

    metrics.forEach((m, i) => {
      if (!m) return;
      const st = this.hands[i];
      const other = this.hands[1 - i];
      const cursor = st.smooth.filter(toScreen(m.tip), now);
      const palm = toScreen(m.palm);
      let progress: number | undefined;

      // ---- Pinch: tap selects, a still hold opens, a moving pinch pans.
      if (!st.pinch) {
        if (m.pinch >= PINCH_ARM) st.arm = { point: cursor, t: now };
        if (m.pinch < PINCH_IN && !m.fist && st.seen >= 2) {
          st.inFrames += 1;
          if (st.inFrames >= PINCH_EARN) {
            st.pinch = true;
            st.pinchT = now;
            st.aim = st.arm && now - st.arm.t <= AIM_LOOKBACK_MS ? st.arm.point : cursor;
            st.pinchPalm = palm;
            st.lastPinchPalm = palm;
            st.panning = false;
            st.held = false;
            st.outFrames = 0;
          }
        } else st.inFrames = 0;
      } else {
        st.outFrames = m.pinch > PINCH_OUT ? st.outFrames + 1 : 0;
        if (st.outFrames >= PINCH_DROP) {
          const tap = !st.panning && !st.held && !this.zoomed && now - st.pinchT < HOLD_MS;
          if (tap && st.aim) actions.push({ type: "select", point: st.aim });
          if (st.panning) {
            actions.push({ type: "release" });
            const fling = flingOf(st.moves, now);
            if (fling) actions.push(fling);
          }
          st.moves = [];
          Object.assign(st, { pinch: false, inFrames: 0, outFrames: 0, aim: null, pinchPalm: null, lastPinchPalm: null, panning: false, held: false });
          if (!this.hands.some((h) => h.pinch)) this.zoomed = false;
        } else if (!this.zoomed && !(both && other.pinch) && st.pinchPalm && st.lastPinchPalm) {
          const travel = Math.hypot(palm.x - st.pinchPalm.x, palm.y - st.pinchPalm.y);
          if (!st.panning && !st.held && travel > TAP_SLOP) st.panning = true;
          if (st.panning) {
            const dx = palm.x - st.lastPinchPalm.x;
            const dy = palm.y - st.lastPinchPalm.y;
            if (Math.hypot(dx, dy) > DRAG_DEADZONE) actions.push({ type: "drag", dx, dy });
            st.moves.push({ x: palm.x, y: palm.y, t: now });
            while (st.moves.length && now - st.moves[0].t > FLICK_WINDOW_MS * 2) st.moves.shift();
            live.push({ id: "pan" });
          } else if (!st.held) {
            progress = clampProgress((now - st.pinchT) / HOLD_MS);
            if (now - st.pinchT >= HOLD_MS && st.aim) {
              st.held = true;
              actions.push({ type: "open", point: st.aim });
            }
            live.push(st.held ? { id: "open", progress: 1 } : { id: "pinch", progress });
          } else live.push({ id: "open", progress: 1 });
          st.lastPinchPalm = palm;
        }
      }

      // ---- Open palm closed to a fist at once: pull in (the Hands view's force pull).
      if (m.open) st.openAt = { t: now, since: st.openAt?.since ?? now, point: cursor };
      else if (st.openAt && now - st.openAt.t > PULL_MS) st.openAt = null;
      if (m.fist && !st.pinch && st.fistFrames === 0 && st.openAt) {
        const shown = st.openAt.t - st.openAt.since >= PULL_SHOWN_MS;
        if (shown && now - st.openAt.t <= PULL_MS && now - this.lastPull >= PULL_COOLDOWN_MS) {
          this.lastPull = now;
          actions.push({ type: "pull", point: st.openAt.point });
        }
        st.openAt = null;
      }

      // ---- Fist: grab the map and move it with the hand.
      if (m.fist && !st.pinch) {
        st.fistFrames += 1;
        if (st.fistFrames >= FIST_EARN && !other.grabbing) {
          if (st.grabbing && st.lastFistPalm) {
            const dx = palm.x - st.lastFistPalm.x;
            const dy = palm.y - st.lastFistPalm.y;
            if (Math.hypot(dx, dy) > DRAG_DEADZONE) actions.push({ type: "drag", dx, dy });
          }
          st.grabbing = true;
          st.lastFistPalm = palm;
          st.moves.push({ x: palm.x, y: palm.y, t: now });
          while (st.moves.length && now - st.moves[0].t > FLICK_WINDOW_MS * 2) st.moves.shift();
          live.push({ id: "pan" });
        }
      } else {
        if (st.grabbing) {
          actions.push({ type: "release" });
          const fling = flingOf(st.moves, now);
          if (fling) actions.push(fling);
        }
        if (!st.pinch) st.moves = [];
        st.grabbing = false;
        st.fistFrames = 0;
        st.lastFistPalm = null;
      }

      // ---- Peace sign held: reset the view (the Hands view's own reset pose).
      if (m.peace && !st.pinch) {
        st.peaceSince ??= now;
        const held = now - st.peaceSince;
        // Once per pose: holding on past a reset (or its cooldown) does not repeat it.
        if (!st.peaceFired && held >= PEACE_MS) {
          st.peaceFired = true;
          this.fireReset(actions, now);
        }
        progress = st.peaceFired ? 1 : clampProgress(held / PEACE_MS);
        live.push({ id: "reset", progress });
      } else {
        st.peaceSince = null;
        st.peaceFired = false;
      }

      // ---- Thumbs up held: confirm (open the selected node).
      if (m.thumbsUp && !st.pinch) {
        st.thumbSince ??= now;
        const held = now - st.thumbSince;
        if (!st.thumbFired && held >= THUMBS_MS) {
          st.thumbFired = true;
          actions.push({ type: "confirm" });
        }
        progress = st.thumbFired ? 1 : clampProgress(held / THUMBS_MS);
        live.push({ id: "confirm", progress });
      } else {
        st.thumbSince = null;
        st.thumbFired = false;
      }

      // ---- Open palm swiped sideways: previous / next section.
      if (m.open && !st.pinch) {
        st.trail.push({ x: palm.x, y: palm.y, t: now });
        while (st.trail.length && now - st.trail[0].t > SWIPE_MS) st.trail.shift();
        const first = st.trail[0];
        const dx = palm.x - first.x;
        const dy = palm.y - first.y;
        if (st.trail.length >= 3 && Math.abs(dx) >= SWIPE_DIST && Math.abs(dx) > 2 * Math.abs(dy)) {
          const step: 1 | -1 = dx > 0 ? 1 : -1;
          const last = this.lastSwipe;
          const wait = last && last.step !== step ? SWIPE_REVERSE_MS : SWIPE_COOLDOWN_MS;
          if (!last || now - last.t > wait) {
            actions.push({ type: "section", step });
            this.lastSwipe = { t: now, step };
          }
          st.trail = [];
        }
        live.push({ id: "swipe" });
      } else st.trail = [];

      // ---- One open palm held still: push the map back (the Hands view's repulsor).
      // Two open palms are the reset pose, so the other hand must not be open too.
      const otherOpen = metrics[1 - i]?.open ?? false;
      if (m.open && !st.pinch && !otherOpen) {
        const moved = st.pushAnchor && Math.hypot(palm.x - st.pushAnchor.x, palm.y - st.pushAnchor.y) > STILL_SLOP;
        if (st.pushSince === null || moved) {
          st.pushSince = now;
          st.pushAnchor = palm;
          st.pushFired = false;
        }
        const held = now - st.pushSince;
        if (!st.pushFired && held >= PUSH_MS && now - this.lastPush >= PUSH_COOLDOWN_MS) {
          st.pushFired = true;
          this.lastPush = now;
          actions.push({ type: "push" });
        }
        if (held > SWIPE_MS) live.push({ id: "push", progress: st.pushFired ? 1 : clampProgress(held / PUSH_MS) });
      } else {
        st.pushSince = null;
        st.pushAnchor = null;
        st.pushFired = false;
      }

      const pose: HandPose = st.pinch
        ? "pinch"
        : m.thumbsUp
          ? "thumbs"
          : m.fist
            ? "fist"
            : m.peace
              ? "peace"
              : m.open
                ? "palm"
                : m.point
                  ? "point"
                  : "other";
      // While pinching, the cursor stays on what the pinch aims at and moves
      // with the hand, so what the ring sits on is what gets picked.
      const shown =
        st.pinch && st.aim && st.pinchPalm
          ? { x: clamp01(st.aim.x + palm.x - st.pinchPalm.x), y: clamp01(st.aim.y + palm.y - st.pinchPalm.y) }
          : cursor;
      cursors.push({ slot: i, pose, ...shown, ...(progress !== undefined ? { progress } : {}) });
    });

    // ---- Two pinching hands zoom by the change in the distance between them.
    const [a, b] = this.hands;
    if (both && a.pinch && b.pinch && metrics[0] && metrics[1]) {
      const pa = toScreen(metrics[0].pinchAt);
      const pb = toScreen(metrics[1].pinchAt);
      const d = dist(pa, pb);
      if (this.zoomFrom !== null && this.zoomFrom > 1e-3 && d > 1e-3) {
        const scale = d / this.zoomFrom;
        if (Math.abs(scale - 1) > 0.004) actions.push({ type: "zoom", scale });
      }
      // Twisting the two pinches turns the map, as in the Hands view.
      const angle = Math.atan2(pb.y - pa.y, pb.x - pa.x);
      if (this.twistFrom !== null) {
        const delta = turn(this.twistFrom, angle);
        if (Math.abs(delta) > TWIST_DEADZONE) {
          actions.push({ type: "rotate", angle: delta });
          live.unshift({ id: "rotate" });
        }
      }
      this.twistFrom = angle;
      // A one-hand pan that turned into a zoom has let go of the map.
      for (const h of this.hands) {
        if (h.panning) actions.push({ type: "release" });
        h.panning = false;
      }
      this.zoomFrom = d;
      this.zoomed = true;
      live.unshift({ id: "zoom" });
    } else {
      this.zoomFrom = null;
      this.twistFrom = null;
    }

    // ---- Both palms open and still: reset the view.
    if (both && metrics[0]?.open && metrics[1]?.open && !a.pinch && !b.pinch) {
      const palms = [toScreen((metrics[0] as HandMetrics).palm), toScreen((metrics[1] as HandMetrics).palm)];
      const moved = this.twoPalm?.anchor.some((p, k) => Math.hypot(p.x - palms[k].x, p.y - palms[k].y) > STILL_SLOP);
      if (!this.twoPalm || moved) this.twoPalm = { since: now, anchor: palms, fired: false };
      const held = now - this.twoPalm.since;
      if (!this.twoPalm.fired && held >= TWO_PALM_MS) {
        this.twoPalm.fired = true;
        this.fireReset(actions, now);
      }
      live.unshift({ id: "reset", progress: this.twoPalm.fired ? 1 : clampProgress(held / TWO_PALM_MS) });
    } else this.twoPalm = null;

    // ---- Hover follows the first pointing (or relaxed) hand that is not busy.
    if (!this.zoomed) {
      const hover = cursors.find(
        (c) => (c.pose === "point" || c.pose === "other") && this.hands[c.slot].seen >= HOVER_EARN,
      );
      if (hover) {
        actions.push({ type: "hover", point: { x: hover.x, y: hover.y } });
        live.push({ id: "hover" });
      }
    }

    const rank = (g: LiveGesture) =>
      ["rotate", "zoom", "reset", "push", "open", "pinch", "confirm", "pan", "swipe", "hover"].indexOf(g.id);
    const gesture = live.length ? [...live].sort((x, y) => rank(x) - rank(y))[0] : null;
    return { actions, cursors, gesture };
  }
}

export type SynthPose = "open" | "pinch" | "point" | "fist" | "peace" | "thumbs";

/**
 * A plausible right hand at (x, y) in camera coordinates, in one pose. Ported
 * from the Hands view's own simulator (holo/holo.html `synthHand`, MIT) so the
 * tests feed the engine the same shapes that page was verified against; the
 * thumbs-up is a fist with the thumb raised.
 */
export function synthHand(x: number, y: number, pose: SynthPose, scale = 0.09): HandLandmarks {
  const s = scale;
  const lm: Landmark[] = [];
  const curledHand = pose === "fist" || pose === "thumbs";
  const spread = pose === "open" ? 1.0 : curledHand ? 0.38 : 0.78;
  lm[0] = { x, y: y + s * 0.9 };
  lm[9] = { x, y: y - s * 0.1 };
  lm[1] = { x: x - s * 0.35, y: y + s * 0.55 };
  lm[2] = { x: x - s * 0.5, y: y + s * 0.3 };
  lm[3] = { x: x - s * 0.55, y: y + s * 0.1 };
  lm[5] = { x: x - s * 0.3, y: y - s * 0.15 };
  lm[13] = { x: x + s * 0.15, y: y - s * 0.15 };
  lm[17] = { x: x + s * 0.32, y: y - s * 0.1 };
  const tipY = y - s * (curledHand ? 0.35 : 1.05) * spread;
  lm[8] = {
    x: pose === "pinch" ? x - s * 0.18 : x - s * 0.28 * (pose === "point" ? 1 : spread),
    y: pose === "pinch" ? y - s * 0.5 : pose === "point" ? y - s * 1.05 : tipY,
  };
  lm[4] = pose === "pinch" ? { x: x - s * 0.16, y: y - s * 0.48 } : { x: x - s * 0.62 * spread, y: y + s * 0.05 };
  if (pose === "thumbs") {
    lm[2] = { x: x - s * 0.45, y: y + s * 0.1 };
    lm[3] = { x: x - s * 0.5, y: y - s * 0.3 };
    lm[4] = { x: x - s * 0.52, y: y - s * 0.8 };
  }
  const curlY = y - s * 0.133;
  const folded = pose === "point" || pose === "peace";
  lm[12] = { x: x - s * 0.05, y: pose === "point" ? curlY : tipY };
  lm[16] = { x: x + s * 0.16, y: (folded ? curlY : tipY) + s * 0.06 };
  lm[20] = { x: x + s * 0.3, y: (folded ? curlY : tipY) + s * 0.14 };
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
