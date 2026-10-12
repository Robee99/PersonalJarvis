/**
 * The HUD memory orb's geometry and voice matching, kept pure so they are
 * testable without WebGL.
 *
 * The tree comes from `GET /api/wiki/orb-tree` (jarvis/memory/orb_tree.py):
 * top-level communities (projects, vault areas), sub-communities (link
 * communities inside a large area) and leaves (a project's items, notes).
 * The layout puts it on three shells around the core, GraphRAG's levels
 * turned into depth:
 *
 *  - level-1 communities on an inner sphere, spread evenly (Fibonacci);
 *  - level-2 communities on the same shell, in a cap around their parent;
 *  - leaves on an outer shell, in a cap around their community, the cap
 *    growing with the square root of the community's size.
 *
 * Deterministic: the same tree always lands in the same place, so a person
 * finds a project where they left it.
 */

export type OrbColour = "red" | "yellow" | "blue" | "green" | "reference";

export interface OrbRef {
  type: "project" | "entity" | "note";
  id?: string;
  slug?: string;
}

export interface OrbCommunity {
  id: string;
  title: string;
  level: 1 | 2;
  parent: string | null;
  kind: "project" | "area" | "notes";
  colour: OrbColour;
  reason: string;
  progress: number | null;
  ref: OrbRef | null;
  size: number;
}

export interface OrbLeaf {
  id: string;
  title: string;
  kind: string;
  colour: OrbColour;
  community: string;
  ref: OrbRef;
}

export interface OrbTree {
  communities: OrbCommunity[];
  leaves: OrbLeaf[];
  counts: Record<OrbColour, number>;
  totals: { notes: number; projects: number };
  legend: Record<OrbColour, string>;
  unavailable: string[];
}

export type Vec3 = [number, number, number];

export interface PlacedNode {
  id: string;
  title: string;
  colour: OrbColour;
  pos: Vec3;
  /** The community a leaf belongs to; a level-2 community's parent; null at level 1. */
  parent: string | null;
  level: 1 | 2 | 3;
  ref: OrbRef | null;
  size: number;
}

export interface OrbLayout {
  nodes: PlacedNode[];
  byId: Map<string, PlacedNode>;
  /** Leaves of each community, in the order the stepper walks them. */
  leavesOf: Map<string, PlacedNode[]>;
  /** Communities in the order the stepper walks them (level 1, then their subs). */
  communityOrder: string[];
}

/** HUD palette: the brain map's health colours, and a calm teal for reference notes. */
export const ORB_HEX: Record<OrbColour, string> = {
  red: "#ff5d6c",
  yellow: "#ffc145",
  blue: "#4f9dff",
  green: "#3ddc97",
  reference: "#3fb6c8",
};

export const INNER_RADIUS = 1;
export const OUTER_RADIUS = 1.55;

const GOLDEN = Math.PI * (3 - Math.sqrt(5));

/** `n` evenly spread unit vectors (Fibonacci sphere); index 0 near the top. */
export function fibonacciSphere(n: number): Vec3[] {
  if (n <= 0) return [];
  if (n === 1) return [[0, 0, 1]];
  const out: Vec3[] = [];
  for (let i = 0; i < n; i++) {
    const y = 1 - (i / (n - 1)) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = GOLDEN * i;
    out.push([Math.cos(theta) * r, y, Math.sin(theta) * r]);
  }
  return out;
}

function normalise(v: Vec3): Vec3 {
  const len = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / len, v[1] / len, v[2] / len];
}

function cross(a: Vec3, b: Vec3): Vec3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

/**
 * `n` unit vectors in a cap of angular radius `spread` around `axis`, on a
 * sunflower spiral so the cap fills evenly whatever `n` is.
 */
export function capAround(axis: Vec3, n: number, spread: number): Vec3[] {
  const a = normalise(axis);
  const helper: Vec3 = Math.abs(a[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0];
  const u = normalise(cross(a, helper));
  const v = cross(a, u);
  const out: Vec3[] = [];
  for (let i = 0; i < n; i++) {
    const t = n === 1 ? 0 : Math.sqrt((i + 0.5) / n);
    const angle = t * spread;
    const phi = GOLDEN * i;
    const s = Math.sin(angle);
    const c = Math.cos(angle);
    out.push(
      normalise([
        a[0] * c + (u[0] * Math.cos(phi) + v[0] * Math.sin(phi)) * s,
        a[1] * c + (u[1] * Math.cos(phi) + v[1] * Math.sin(phi)) * s,
        a[2] * c + (u[2] * Math.cos(phi) + v[2] * Math.sin(phi)) * s,
      ]),
    );
  }
  return out;
}

const scale = (v: Vec3, r: number): Vec3 => [v[0] * r, v[1] * r, v[2] * r];

/** Where everything in the tree sits. See the module docstring. */
export function layoutOrb(tree: Pick<OrbTree, "communities" | "leaves">): OrbLayout {
  const nodes: PlacedNode[] = [];
  const byId = new Map<string, PlacedNode>();
  const leavesOf = new Map<string, PlacedNode[]>();
  const communityOrder: string[] = [];
  const top = tree.communities.filter((c) => c.level === 1);
  const subsOf = new Map<string, OrbCommunity[]>();
  for (const c of tree.communities) {
    if (c.level === 2 && c.parent) {
      const list = subsOf.get(c.parent) ?? [];
      list.push(c);
      subsOf.set(c.parent, list);
    }
  }
  const leafGroups = new Map<string, OrbLeaf[]>();
  for (const leaf of tree.leaves) {
    const list = leafGroups.get(leaf.community) ?? [];
    list.push(leaf);
    leafGroups.set(leaf.community, list);
  }
  const dirs = fibonacciSphere(top.length);
  // How wide a community's territory is: the even share of the sphere each
  // top-level community gets, so neighbours never overlap.
  const territory = top.length > 1 ? Math.min(0.9, 2 / Math.sqrt(top.length)) : 1.2;

  const place = (node: PlacedNode) => {
    nodes.push(node);
    byId.set(node.id, node);
  };
  const placeLeaves = (communityId: string, axis: Vec3, spread: number) => {
    const leaves = leafGroups.get(communityId) ?? [];
    const cap = capAround(axis, leaves.length, Math.min(spread, 0.08 + 0.05 * Math.sqrt(leaves.length)));
    const placed = leaves.map((leaf, i) => {
      const node: PlacedNode = {
        id: leaf.id,
        title: leaf.title,
        colour: leaf.colour,
        pos: scale(cap[i], OUTER_RADIUS),
        parent: communityId,
        level: 3,
        ref: leaf.ref,
        size: 1,
      };
      place(node);
      return node;
    });
    leavesOf.set(communityId, placed);
  };

  top.forEach((community, i) => {
    const axis = dirs[i];
    place({
      id: community.id,
      title: community.title,
      colour: community.colour,
      pos: scale(axis, INNER_RADIUS),
      parent: null,
      level: 1,
      ref: community.ref,
      size: community.size,
    });
    communityOrder.push(community.id);
    const subs = subsOf.get(community.id) ?? [];
    if (subs.length === 0) {
      placeLeaves(community.id, axis, territory * 0.8);
      return;
    }
    const subDirs = capAround(axis, subs.length, territory * 0.55);
    subs.forEach((sub, j) => {
      place({
        id: sub.id,
        title: sub.title,
        colour: sub.colour,
        pos: scale(subDirs[j], INNER_RADIUS * 1.12),
        parent: community.id,
        level: 2,
        ref: sub.ref,
        size: sub.size,
      });
      communityOrder.push(sub.id);
      placeLeaves(sub.id, subDirs[j], (territory * 0.55) / Math.max(1, Math.sqrt(subs.length)));
    });
  });
  return { nodes, byId, leavesOf, communityOrder };
}

/**
 * At most `max` leaves, shared fairly: communities take turns, one leaf each,
 * so a huge imported area cannot crowd every project off the orb. The kept
 * leaves stay in their original order.
 */
export function capLeaves(leaves: OrbLeaf[], max: number): OrbLeaf[] {
  if (leaves.length <= max) return leaves;
  const groups = new Map<string, OrbLeaf[]>();
  for (const leaf of leaves) {
    const list = groups.get(leaf.community) ?? [];
    list.push(leaf);
    groups.set(leaf.community, list);
  }
  const kept = new Set<OrbLeaf>();
  const lists = [...groups.values()];
  for (let round = 0; kept.size < max; round++) {
    let took = false;
    for (const list of lists) {
      if (kept.size >= max) break;
      if (round < list.length) {
        kept.add(list[round]);
        took = true;
      }
    }
    if (!took) break;
  }
  return leaves.filter((leaf) => kept.has(leaf));
}

/** The next or previous leaf of the same community, wrapping around. */
export function stepLeaf(layout: OrbLayout, currentId: string, delta: number): PlacedNode | null {
  const current = layout.byId.get(currentId);
  if (!current) return null;
  const communityId = current.level === 3 ? current.parent : current.id;
  const leaves = communityId ? (layout.leavesOf.get(communityId) ?? []) : [];
  if (leaves.length === 0) return null;
  const at = leaves.findIndex((l) => l.id === currentId);
  if (at < 0) return leaves[delta >= 0 ? 0 : leaves.length - 1];
  return leaves[(at + delta + leaves.length * 4) % leaves.length];
}

/** The next or previous community that has something in it, wrapping around. */
export function stepCommunity(layout: OrbLayout, currentId: string | null, delta: number): PlacedNode | null {
  const order = layout.communityOrder;
  if (order.length === 0) return null;
  const current = currentId ? layout.byId.get(currentId) : undefined;
  const communityId = current?.level === 3 ? current.parent : (current?.id ?? null);
  const at = communityId ? order.indexOf(communityId) : -1;
  const next = at < 0 ? (delta >= 0 ? 0 : order.length - 1) : (at + delta + order.length * 4) % order.length;
  return layout.byId.get(order[next]) ?? null;
}

const STOP = new Set(["notes", "note", "import", "loose", "the", "and", "workspace"]);

/**
 * The node a spoken sentence names: the longest title that appears in it as
 * whole words. Short or generic titles never match, so "open the notes"
 * does not jump anywhere. Communities win a tie over leaves.
 */
export function nodeNamedIn(layout: OrbLayout, sentence: string): PlacedNode | null {
  const said = ` ${sentence.toLowerCase().replace(/[^a-z0-9]+/g, " ")} `;
  if (said.trim().length < 4) return null;
  let best: PlacedNode | null = null;
  let bestLen = 0;
  for (const node of layout.nodes) {
    const name = node.title
      .toLowerCase()
      .replace(/^import · /, "")
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
    if (name.length < 5 || STOP.has(name)) continue;
    if (!said.includes(` ${name} `)) continue;
    const better = name.length > bestLen || (name.length === bestLen && best !== null && node.level < best.level);
    if (better) {
      best = node;
      bestLen = name.length;
    }
  }
  return best;
}
