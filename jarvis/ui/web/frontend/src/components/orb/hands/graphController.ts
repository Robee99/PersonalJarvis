/**
 * Gesture actions onto a force-graph canvas, through the same handlers the
 * mouse uses: hover sets the hovered node, a pinch is a click (a second pinch
 * on the picked node opens it), an open-palm drag moves the camera over the
 * map the way a mouse drag pans it, and a two-hand pinch zooms around the
 * centre. The 2-D map has no rotation axis, so "rotate" is this orbit of the
 * view rather than a spin of the drawing.
 *
 * Kept apart from the view so the mapping is testable with a fake graph.
 */
import type { GestureAction, ScreenPoint } from "./gestures";
import type { HandController } from "./useHandTracking";

/** The few force-graph methods hand control needs (react-force-graph-2d). */
export interface GraphCamera {
  screen2GraphCoords(x: number, y: number): { x: number; y: number };
  centerAt(): { x: number; y: number };
  centerAt(x: number, y: number, ms?: number): unknown;
  zoom(): number;
  zoom(k: number, ms?: number): unknown;
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
}

/** Screen pixels within which a hand "touches" a node; fingers are coarse. */
export const HAND_REACH_PX = 28;
export const MIN_ZOOM = 0.15;
export const MAX_ZOOM = 14;

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
  return {
    onAction(action: GestureAction) {
      const graph = target.graph();
      if (!graph) return;
      if (action.type === "hover") {
        target.hover(nodeAt(target, action.point));
      } else if (action.type === "select") {
        const node = nodeAt(target, action.point);
        if (node && target.picked()?.id === node.id) target.open(node);
        else target.pick(node);
      } else if (action.type === "drag") {
        const { width, height } = target.size();
        const k = Math.max(graph.zoom(), 1e-3);
        const c = graph.centerAt();
        // The map follows the hand, like grabbing it: the camera moves the other way.
        graph.centerAt(c.x - (action.dx * width) / k, c.y - (action.dy * height) / k);
      } else if (action.type === "zoom") {
        graph.zoom(Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, graph.zoom() * action.scale)));
      }
    },
  };
}
