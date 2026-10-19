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
import { buildSections, type OrbSections } from "@/lib/orbSections";

/** A real wikilink, a community's source note, a spoke to a family core, a spoke to a section's branch point. */
export type SceneLinkKind = "note" | "source" | "family" | "section";

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
