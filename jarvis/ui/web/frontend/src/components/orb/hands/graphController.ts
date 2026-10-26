/**
 * Gesture actions onto a map of nodes, through the same handlers the mouse
 * uses. `handMapController` is the mapping itself, over a small `HandMap`
 * interface any map can provide; `graphHandController` builds that interface
 * for a force-graph canvas (react-force-graph-2d), and `sceneBridge` lets the
 * 3D map take the hand's hover and zoom as pointer and wheel input.
 *
 *   hover   → the node nearest the cursor within a finger's reach is hovered
 *   select  → that node is picked; the picked node is opened; empty space
 *             clears the pick
 *   open    → that node (or else the picked one) is opened
 *   confirm → the picked node (or else the hovered one) is opened
 *   drag    → the map follows the hand, like grabbing it
 *   zoom    → zoom around the centre
 *   section → previous / next section
 *   reset   → the whole map framed again, nothing picked
 *   rotate  → the map turns (the 3D map orbits; the flat map has no turn)
 *   fling   → the map keeps gliding the way the hand threw it, slowing down
 *   pull    → fly in to the node under the cursor (or zoom in)
 *   push    → zoom out a step
 *
 * A pick by hand only marks the node. Recentring on it would slide it out
 * from under the hand, and the second pinch that opens it would land on
 * whatever moved in instead.
 *
 * Kept apart from the views so the mapping is testable with a fake map.
 */
import type { GestureAction, ScreenPoint } from "./gestures";
import type { HandController } from "./useHandTracking";

/** What a map gives hand control: callbacks, so each view wires its own. */
export interface HandMap<N extends { id: string }> {
  /** The node under (or nearest to) a point on the map, 0..1 on both axes. */
  nodeAt(point: ScreenPoint): N | null;
  picked(): N | null;
  hover(node: N | null): void;
  pick(node: N | null): void;
  open(node: N): void;
  /** Move the map with the hand by this much of its width and height. */
  pan?(dx: number, dy: number): void;
  zoom?(scale: number): void;
  reset?(): void;
  section?(step: 1 | -1): void;
  rotate?(angle: number): void;
  /** Fly in to this node; without it a pull zooms in. */
  pull?(node: N): void;
}

/** Zoom steps of a pull with nothing under the cursor, and of a push. */
export const PULL_ZOOM = 1.4;
export const PUSH_ZOOM = 0.7;
/** How long a flick glides, and how fast it slows (per 60 fps frame). */
export const GLIDE_MS = 900;
export const GLIDE_DECAY = 0.9;

/**
 * Keep moving at `vx, vy` (units per second), slowing each frame, by calling
 * `step` with each frame's share. A new glide or `stop()` ends the last one.
 */
export function glider(step: (dx: number, dy: number) => void) {
  let raf: number | undefined;
  const stop = () => {
    if (raf !== undefined) cancelAnimationFrame(raf);
    raf = undefined;
  };
  return {
    stop,
    start(vx: number, vy: number) {
      stop();
      let last = performance.now();
      const began = last;
      let ux = vx;
      let uy = vy;
      const tick = (now: number) => {
        const dt = Math.min(0.05, Math.max(0, (now - last) / 1000));
        last = now;
        step(ux * dt, uy * dt);
        const decay = Math.pow(GLIDE_DECAY, dt * 60);
        ux *= decay;
        uy *= decay;
        if (now - began < GLIDE_MS && Math.hypot(ux, uy) > 0.02) raf = requestAnimationFrame(tick);
        else raf = undefined;
      };
      raf = requestAnimationFrame(tick);
    },
  };
}

export function handMapController<N extends { id: string }>(map: HandMap<N>): HandController {
  let hovered: N | null = null;
  const glide = glider((dx, dy) => map.pan?.(dx, dy));
  return {
    onAction(action: GestureAction) {
      switch (action.type) {
        case "hover": {
          hovered = map.nodeAt(action.point);
          map.hover(hovered);
          return;
        }
        case "select": {
          const node = map.nodeAt(action.point);
          if (node && map.picked()?.id === node.id) map.open(node);
          else map.pick(node);
          return;
        }
        case "open": {
          const node = map.nodeAt(action.point) ?? map.picked();
          if (node) map.open(node);
          return;
        }
        case "confirm": {
          const node = map.picked() ?? hovered;
          if (node) map.open(node);
          return;
        }
        case "drag":
          glide.stop();
          map.pan?.(action.dx, action.dy);
          return;
        case "fling":
          if (map.pan) glide.start(action.vx, action.vy);
          return;
        case "rotate":
          map.rotate?.(action.angle);
          return;
        case "pull": {
          const node = map.nodeAt(action.point) ?? hovered;
          if (node && map.pull) map.pull(node);
          else map.zoom?.(PULL_ZOOM);
          return;
        }
        case "push":
          map.zoom?.(PUSH_ZOOM);
          return;
        case "zoom":
          map.zoom?.(action.scale);
          return;
        case "section":
          map.section?.(action.step);
          return;
        case "reset":
          glide.stop();
          hovered = null;
          map.hover(null);
          map.pick(null);
          map.reset?.();
          return;
        case "release":
          return;
      }
    },
  };
}

/** The few force-graph methods hand control needs (react-force-graph-2d). */
export interface GraphCamera {
  screen2GraphCoords(x: number, y: number): { x: number; y: number };
  centerAt(): { x: number; y: number };
  centerAt(x: number, y: number, ms?: number): unknown;
  zoom(): number;
  zoom(k: number, ms?: number): unknown;
  zoomToFit?(ms?: number, padding?: number): unknown;
}

export interface PlacedNode {
  id: string;
  x?: number;
  y?: number;
}

export interface GraphHandTarget<N extends PlacedNode> {
  graph(): GraphCamera | undefined;
  size(): { width: number; height: number };
  nodes(): N[];
  picked(): N | null;
  hover(node: N | null): void;
  pick(node: N | null): void;
  open(node: N): void;
  /** Frame the map again; without it the whole graph is zoomed to fit. */
  reset?(): void;
  /** Jump to the previous / next section; without it a swipe does nothing. */
  section?(step: 1 | -1): void;
}

/**
 * Screen pixels within which a hand "touches" a node. A fingertip seen by a
 * webcam wobbles by more than a mouse ever does, so the cursor snaps to the
 * nearest node this close.
 */
export const HAND_REACH_PX = 44;
export const MIN_ZOOM = 0.15;
export const MAX_ZOOM = 14;
export const FIT_PADDING_PX = 60;

export function nodeAt<N extends PlacedNode>(
  target: Pick<GraphHandTarget<N>, "graph" | "size" | "nodes">,
  point: ScreenPoint,
): N | null {
  const graph = target.graph();
  if (!graph) return null;
  const { width, height } = target.size();
  const at = graph.screen2GraphCoords(point.x * width, point.y * height);
  const reach = HAND_REACH_PX / Math.max(graph.zoom(), 1e-3);
  let best: N | null = null;
  let bestD = reach;
  for (const node of target.nodes()) {
    if (node.x === undefined || node.y === undefined) continue;
    const d = Math.hypot(node.x - at.x, node.y - at.y);
    if (d <= bestD) {
      bestD = d;
      best = node;
    }
  }
  return best;
}

export function graphHandController<N extends PlacedNode>(target: GraphHandTarget<N>): HandController {
  return handMapController<N>({
    nodeAt: (point) => nodeAt(target, point),
    picked: () => target.picked(),
    hover: (node) => target.hover(node),
    pick: (node) => target.pick(node),
    open: (node) => target.open(node),
    pan(dx, dy) {
      const graph = target.graph();
      if (!graph) return;
      const { width, height } = target.size();
      const k = Math.max(graph.zoom(), 1e-3);
      const c = graph.centerAt();
      // The map follows the hand, like grabbing it: the camera moves the other way.
      graph.centerAt(c.x - (dx * width) / k, c.y - (dy * height) / k);
    },
    zoom(scale) {
      const graph = target.graph();
      if (!graph) return;
      graph.zoom(Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, graph.zoom() * scale)));
    },
    reset() {
      if (target.reset) return target.reset();
      const graph = target.graph();
      if (!graph) return;
      if (graph.zoomToFit) graph.zoomToFit(600, FIT_PADDING_PX);
      else {
        graph.centerAt(0, 0, 600);
        graph.zoom(1, 600);
      }
    },
    section: target.section ? (step) => target.section?.(step) : undefined,
    pull(node) {
      const graph = target.graph();
      if (!graph || node.x === undefined || node.y === undefined) return;
      graph.centerAt(node.x, node.y, 400);
      graph.zoom(Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, graph.zoom() * PULL_ZOOM)), 400);
    },
  });
}

/** OrbitControls turns one wheel "notch" of deltaY d into a 0.95^(|d|/100) dolly. */
export function wheelDeltaFor(scale: number): number {
  if (!(scale > 0)) return 0;
  return (-100 * Math.log(scale)) / -Math.log(0.95);
}

export interface SceneBridge {
  /** Put the 3D map's pointer at this point, so its own picking hovers the node. */
  move(point: ScreenPoint): boolean;
  /** Zoom by this factor through the controls' own wheel handling. */
  zoom(scale: number): boolean;
}

/**
 * The 3D map (react-force-graph-3d) picks nodes with its own raycaster from
 * the pointer and zooms with its orbit controls' wheel handler. A hand drives
 * both through the same events the mouse sends: a pointer move on the scene
 * container and a wheel on its canvas. Neither starts an orbit drag (that
 * needs a pointer press on the canvas), so the mouse keeps its own state.
 */
export function sceneBridge(canvas: () => HTMLCanvasElement | null): SceneBridge {
  // Chromium (the desktop WebView) has PointerEvent; the library only reads
  // the event type and the coordinates, which a MouseEvent carries too.
  const pointer = (init: MouseEventInit) =>
    typeof PointerEvent === "function"
      ? new PointerEvent("pointermove", { ...init, pointerType: "mouse" })
      : new MouseEvent("pointermove", init);
  const at = (el: HTMLElement, point: ScreenPoint) => {
    const rect = el.getBoundingClientRect();
    return { clientX: rect.left + point.x * rect.width, clientY: rect.top + point.y * rect.height };
  };
  return {
    move(point) {
      const cv = canvas();
      const container = cv?.parentElement;
      if (!cv || !container) return false;
      container.dispatchEvent(pointer({ bubbles: true, ...at(cv, point) }));
      return true;
    },
    zoom(scale) {
      const cv = canvas();
      const deltaY = wheelDeltaFor(scale);
      if (!cv || !deltaY) return false;
      cv.dispatchEvent(new WheelEvent("wheel", { bubbles: true, cancelable: true, deltaY, ...at(cv, { x: 0.5, y: 0.5 }) }));
      return true;
    },
  };
}
