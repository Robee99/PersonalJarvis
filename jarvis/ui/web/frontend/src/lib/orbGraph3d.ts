/**
 * The memory orb's map laid out in space.
 *
 * The flat map shows each family (notes, skills, tools, apps, MCP servers) as
 * a labelled region. In space a region has no edge to write its name on, so
 * each family gets a core: a larger sphere carrying the family's name, with
 * faint spokes to its members. A core is the family label made spatial, not
 * a memory: it is never listed, searched or opened, and spokes are drawn
 * fainter than the real links so the two never read as the same thing.
 *
 * A family with several sections (`lib/orbSections.ts`) branches once more:
 * each section gets a branch point pinned round its family's core, and its
 * members hang off that point, so a section reads as one cluster and a jump
 * to it frames one place. Cores and branch points are neutral; only the
 * memories carry colour.
 *
 * Real links are the vault's own wikilinks (from `buildOrbGraph`) and, for a
 * GraphRAG community, the notes it was built from (`sourceLinks`).
 *
 * Pure, so the shape of the scene is testable without WebGL.
 */
import type { OrbGroup, OrbLink, OrbNode } from "@/lib/orbGraph";
import { buildSections, type OrbSection, type OrbSections } from "@/lib/orbSections";

/**
 * A real wikilink, a community's source note, a spoke to a family core, a
 * spoke to a section's branch point, or two memories whose names share a rare
 * word (`constellation`).
 */
export type SceneLinkKind = "note" | "source" | "family" | "section" | "near";

export interface SceneNode {
  id: string;
  group: OrbGroup;
  /** A family core rather than a memory. */
  core: boolean;
  /** A section's branch point rather than a memory; its id is the section's id. */
  branch?: boolean;
  /** The section a memory or a branch point belongs to. */
  section?: string;
  /** The memory this sphere stands for; absent on a core. */
  node?: OrbNode;
  /** Real links (not spokes) touching this node. */
  degree: number;
  x?: number;
  y?: number;
  z?: number;
  fx?: number;
  fy?: number;
  fz?: number;
}

export interface SceneLink {
  source: string;
  target: string;
  kind: SceneLinkKind;
}

export interface SceneGraph {
  nodes: SceneNode[];
  links: SceneLink[];
}

export const CORE_PREFIX = "family:";

export function coreId(group: OrbGroup): string {
  return `${CORE_PREFIX}${group}`;
}

/**
 * Evenly spread points on a sphere (a Fibonacci lattice), one per family, so
 * the families sit apart in every direction instead of on one ring.
 */
export function familyAnchors(
  groups: readonly OrbGroup[],
  radius: number,
): Map<OrbGroup, { x: number; y: number; z: number }> {
  const anchors = new Map<OrbGroup, { x: number; y: number; z: number }>();
  if (groups.length === 1) {
    anchors.set(groups[0], { x: 0, y: 0, z: 0 });
    return anchors;
  }
  const golden = Math.PI * (3 - Math.sqrt(5));
  groups.forEach((group, i) => {
    const y = 1 - (2 * (i + 0.5)) / groups.length;
    const ring = Math.sqrt(1 - y * y);
    const angle = golden * i;
    anchors.set(group, {
      x: Math.cos(angle) * ring * radius,
      y: y * radius * 0.7,
      z: Math.sin(angle) * ring * radius,
    });
  });
  return anchors;
}

/** How far apart the family cores sit for a map of this many memories. */
export function familyRadius(count: number): number {
  return 55 + 5 * Math.sqrt(count);
}

/** How far a family's branch points sit from its core, for a family of this many memories. */
export function sectionRing(familySize: number, outer: number): number {
  return Math.min(outer * 0.5, 18 + 3 * Math.sqrt(familySize));
}

/** `n` unit vectors spread evenly over a sphere (a Fibonacci lattice). */
function spread(n: number): { x: number; y: number; z: number }[] {
  const golden = Math.PI * (3 - Math.sqrt(5));
  return Array.from({ length: n }, (_, i) => {
    const y = 1 - (2 * (i + 0.5)) / n;
    const ring = Math.sqrt(1 - y * y);
    return { x: Math.cos(golden * i) * ring, y, z: Math.sin(golden * i) * ring };
  });
}

/**
 * The scene for what is shown: every memory, one core per family present,
 * a branch point per section of a family that has several, spokes from
 * members to their branch point (or core) and from branch points to their
 * core, and the real links between memories. Cores and branch points are
 * pinned; members start scattered round the point they hang off, so the
 * first frame is already a constellation, not a ball.
 */
export function buildSceneGraph(
  nodes: readonly OrbNode[],
  links: readonly OrbLink[],
  random: () => number = Math.random,
  sections: OrbSections = buildSections(nodes),
): SceneGraph {
  const ids = new Set(nodes.map((n) => n.id));
  const slugIds = new Map<string, string>();
  for (const n of nodes) if (n.slug) slugIds.set(n.slug, n.id);

  const real: SceneLink[] = [];
  const seen = new Set<string>();
  const push = (a: string, b: string, kind: SceneLinkKind) => {
    if (a === b || !ids.has(a) || !ids.has(b)) return;
    const key = a < b ? `${a}|${b}` : `${b}|${a}`;
    if (seen.has(key)) return;
    seen.add(key);
    real.push({ source: a, target: b, kind });
  };
  for (const link of links) push(link.source, link.target, "note");
  for (const n of nodes) {
    for (const source of n.sourceLinks ?? []) {
      const target = source.slug ? slugIds.get(source.slug) : undefined;
      if (target) push(n.id, target, "source");
    }
  }

  const degree = new Map<string, number>();
  for (const link of real) {
    degree.set(link.source, (degree.get(link.source) ?? 0) + 1);
    degree.set(link.target, (degree.get(link.target) ?? 0) + 1);
  }

  const groups: OrbGroup[] = [];
  const familySize = new Map<OrbGroup, number>();
  for (const n of nodes) {
    if (!groups.includes(n.group)) groups.push(n.group);
    familySize.set(n.group, (familySize.get(n.group) ?? 0) + 1);
  }
  const radius = familyRadius(nodes.length);
  const anchors = familyAnchors(groups, radius);

  const cores: SceneNode[] = groups.map((group) => {
    const a = anchors.get(group)!;
    return { id: coreId(group), group, core: true, degree: 0, x: a.x, y: a.y, z: a.z, fx: a.x, fy: a.y, fz: a.z };
  });

  // Branch points, family by family, spread on a small sphere round the core.
  const branches: SceneNode[] = [];
  const hangFrom = new Map<string, { x: number; y: number; z: number }>();
  for (const group of groups) {
    const mine = sections.list.filter((s) => s.group === group && s.branch && s.memberIds.some((m) => ids.has(m)));
    const a = anchors.get(group)!;
    const ring = sectionRing(familySize.get(group) ?? 0, radius);
    spread(mine.length).forEach((dir, i) => {
      const at = { x: a.x + dir.x * ring, y: a.y + dir.y * ring, z: a.z + dir.z * ring };
      hangFrom.set(mine[i].id, at);
      branches.push({
        id: mine[i].id,
        group,
        core: false,
        branch: true,
        section: mine[i].id,
        degree: 0,
        ...at,
        fx: at.x,
        fy: at.y,
        fz: at.z,
      });
    });
  }

  const scatter = 14 + 1.5 * Math.sqrt(nodes.length);
  const members: SceneNode[] = nodes.map((n) => {
    const section = sections.ofNode.get(n.id);
    const from = (section && hangFrom.get(section.id)) ?? anchors.get(n.group)!;
    const r = section && hangFrom.has(section.id) ? scatter * 0.5 : scatter;
    return {
      id: n.id,
      group: n.group,
      core: false,
      section: section?.id,
      node: n,
      degree: degree.get(n.id) ?? 0,
      x: from.x + (random() - 0.5) * r,
      y: from.y + (random() - 0.5) * r,
      z: from.z + (random() - 0.5) * r,
    };
  });
  const spokes: SceneLink[] = [
    ...branches.map((b): SceneLink => ({ source: b.id, target: coreId(b.group), kind: "family" })),
    ...members.map((m): SceneLink =>
      m.section && hangFrom.has(m.section)
        ? { source: m.id, target: m.section, kind: "section" }
        : { source: m.id, target: coreId(m.group), kind: "family" },
    ),
  ];

  return { nodes: [...cores, ...branches, ...members], links: [...real, ...spokes] };
}

/** Sphere radius, in scene units, for a node: hubs larger, branch points larger still, cores largest. */
export function sphereRadius(node: Pick<SceneNode, "core" | "degree"> & { branch?: boolean }): number {
  if (node.core) return 6.4;
  if (node.branch) return 4.4;
  return Math.min(5.4, 2.3 + Math.sqrt(node.degree) * 0.75);
}

/** A memory with at least this many real links can be a named hub. */
export const HUB_DEGREE = 4;

/** At most this many hubs keep their name on; the rest show it on hover. */
export const MAX_HUB_LABELS = 16;

/** The best-connected memories, which keep their names on at rest. */
export function labelledHubs(nodes: readonly SceneNode[]): Set<string> {
  return new Set(
    nodes
      .filter((n) => !n.core && !n.branch && n.degree >= HUB_DEGREE)
      .sort((a, b) => b.degree - a.degree || a.id.localeCompare(b.id))
      .slice(0, MAX_HUB_LABELS)
      .map((n) => n.id),
  );
}


/** A section needs this many memories to get a hub of its own in the constellation. */
export const HUB_MIN_MEMBERS = 4;
/**
 * A section bigger than this is a catch-all (one whole chat export), not a
 * topic: as a hub it would be one star of hundreds of spokes over the map.
 */
export const HUB_MAX_MEMBERS = 140;
/** Name-sharing links each memory may start. */
export const NEAR_PER_NODE = 1;
/** A word in more names than this is too common to say two memories belong together. */
const NEAR_MAX_SHARE = 30;
/** Two names must share at least this much rarity (summed log inverse frequency). */
const NEAR_MIN_SCORE = 5;

const NEAR_STOP = new Set([
  "about", "after", "again", "analysis", "and", "best", "between", "check", "clarification", "comparison",
  "create", "explained", "explanation", "first", "from", "guide", "help", "ideas", "into", "list", "make",
  "more", "need", "overview", "plan", "question", "request", "review", "should", "summary", "than", "that",
  "their", "there", "these", "this", "tips", "using", "what", "when", "which", "with", "your",
]);

function nameWords(label: string): string[] {
  const words = label.toLowerCase().split(/[^\p{L}\p{N}]+/u);
  return [...new Set(words.filter((w) => w.length >= 4 && !NEAR_STOP.has(w) && !/^\d+$/.test(w)))];
}

/**
 * Links between memories whose names share a rare word, at most
 * `NEAR_PER_NODE` started by each, strongest first. An imported chat export
 * has no wikilinks at all, so without these its notes float unconnected.
 */
export function nearLinks(members: readonly SceneNode[]): SceneLink[] {
  const words = new Map<string, string[]>();
  const index = new Map<string, string[]>();
  for (const n of members) {
    if (!n.node) continue;
    const mine = nameWords(n.node.label);
    words.set(n.id, mine);
    for (const w of mine) {
      const list = index.get(w);
      if (list) list.push(n.id);
      else index.set(w, [n.id]);
    }
  }
  const total = Math.max(2, words.size);
  const weight = (w: string) => Math.log(total / (index.get(w)?.length ?? total));
  const out: SceneLink[] = [];
  const seen = new Set<string>();
  for (const [id, mine] of words) {
    const score = new Map<string, number>();
    for (const w of mine) {
      const list = index.get(w)!;
      if (list.length < 2 || list.length > NEAR_MAX_SHARE) continue;
      for (const other of list) if (other !== id) score.set(other, (score.get(other) ?? 0) + weight(w));
    }
    const best = [...score.entries()]
      .filter(([, v]) => v >= NEAR_MIN_SCORE)
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .slice(0, NEAR_PER_NODE);
    for (const [other] of best) {
      const key = id < other ? `${id}|${other}` : `${other}|${id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ source: id, target: other, kind: "near" });
    }
  }
  return out;
}

/** The catch-all section of notes no topic claimed: too big and too mixed for one hub. */
export function isLooseSection(section: Pick<OrbSection, "id">): boolean {
  return section.id.includes("~loose");
}

/**
 * The scene as a constellation (the owner's reference, 2026-10-25: a
 * force-laid knowledge graph, "clean and neat"). Each section big enough
 * becomes a hub its memories hang off with visible lines; a family without
 * sections hangs off its core the same way. Memories whose names share a
 * rare word are linked across sections, so related topics drift together.
 * Nothing is pinned and no family core sits over the sections, so the
 * forces spread the hubs apart instead of packing them round one point.
 */
export function constellation(scene: SceneGraph, sections: OrbSections): SceneGraph {
  const hubs = new Set<string>();
  const branchIds = new Set<string>();
  const branched = new Set<OrbGroup>();
  for (const n of scene.nodes) {
    if (!n.branch) continue;
    branchIds.add(n.id);
    branched.add(n.group);
    const s = sections.byId.get(n.id);
    const size = s?.memberIds.length ?? 0;
    if (s && size >= HUB_MIN_MEMBERS && size <= HUB_MAX_MEMBERS && !isLooseSection(s)) hubs.add(n.id);
  }
  const nodes = scene.nodes.filter((n) => (n.branch ? hubs.has(n.id) : n.core ? !branched.has(n.group) : true));
  const kept = new Set(nodes.map((n) => n.id));
  const links = scene.links.filter((l) => {
    if (l.kind === "note" || l.kind === "source") return true;
    // A branch point's spoke to its family core would pull every hub into one ball.
    if (l.kind === "family" && branchIds.has(l.source)) return false;
    return kept.has(l.source) && kept.has(l.target);
  });
  const members = nodes.filter((n) => !n.core && !n.branch);
  links.push(...nearLinks(members));
  const degree = new Map<string, number>();
  for (const l of links) {
    degree.set(l.source, (degree.get(l.source) ?? 0) + 1);
    degree.set(l.target, (degree.get(l.target) ?? 0) + 1);
  }
  for (const n of nodes) {
    n.degree = degree.get(n.id) ?? 0;
    delete n.fx;
    delete n.fy;
    delete n.fz;
  }
  return { nodes, links };
}
