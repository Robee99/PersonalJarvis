/**
 * Pure shaping of brain-map projects for the Orb: search, colour filter,
 * sort, limits, and the graph the canvas draws. No fetching, no canvas, so
 * every rule about what is shown is testable on its own.
 *
 * Readability as projects grow comes from three bounds, each counted rather
 * than silently dropped: the rail lists `PAGE` projects at a time, the map
 * draws at most `MAX_MAP_PROJECTS` hubs, and an opened project shows at most
 * `MAX_PER_BRANCH` items of each kind, the rest folded into one "+N" cluster
 * that opens the full list.
 */
import {
  BRANCH_KINDS,
  HEALTH_COLOURS,
  HEALTH_HEX,
  type BranchKind,
  type Project,
  type ProjectThread,
  type StatusColour,
  type ThreadBranch,
} from "./brainMap";

export const PAGE = 40;
export const MAX_MAP_PROJECTS = 80;
export const MAX_PER_BRANCH = 8;

export type SortMode = "priority" | "recent" | "progress" | "name";
export const SORT_MODES: SortMode[] = ["priority", "recent", "progress", "name"];

export interface ProjectFilter {
  query: string;
  colours: Set<StatusColour>;
  sort: SortMode;
}

function normalise(text: string): string {
  return text.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

/** Every word of the query appears in the text (case and accents ignored). */
export function matchesText(text: string, query: string): boolean {
  const words = normalise(query).split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = normalise(text);
  return words.every((w) => hay.includes(w));
}

/** Search a project by its title, summary, health reason and folder. */
export function matchesQuery(project: Project, query: string): boolean {
  return matchesText(`${project.title} ${project.summary} ${project.health.reason} ${project.locator}`, query);
}

const COLOUR_RANK: Record<StatusColour, number> = { red: 0, yellow: 1, blue: 2, green: 3 };

export function sortProjects(projects: Project[], sort: SortMode): Project[] {
  const out = [...projects];
  const byName = (a: Project, b: Project) => a.title.localeCompare(b.title);
  if (sort === "name") return out.sort(byName);
  if (sort === "recent") return out.sort((a, b) => b.last_activity_ms - a.last_activity_ms || byName(a, b));
  if (sort === "progress")
    // Untracked (null) sorts after every measured project.
    return out.sort(
      (a, b) => (b.health.progress ?? -1) - (a.health.progress ?? -1) || byName(a, b),
    );
  return out.sort(
    (a, b) =>
      COLOUR_RANK[a.health.colour] - COLOUR_RANK[b.health.colour] ||
      b.priority_score - a.priority_score ||
      b.last_activity_ms - a.last_activity_ms ||
      byName(a, b),
  );
}

export function filterProjects(projects: Project[], filter: ProjectFilter): Project[] {
  const kept = projects.filter(
    (p) => (filter.colours.size === 0 || filter.colours.has(p.health.colour)) && matchesQuery(p, filter.query),
  );
  return sortProjects(kept, filter.sort);
}

export function colourCounts(projects: Project[]): Record<StatusColour, number> {
  const out: Record<StatusColour, number> = { red: 0, yellow: 0, blue: 0, green: 0 };
  for (const p of projects) out[p.health.colour] += 1;
  return out;
}

export { HEALTH_COLOURS };

// ---- the map ----------------------------------------------------------------

export type MapNodeType = "project" | "item" | "cluster";

export interface MapNode {
  id: string;
  type: MapNodeType;
  label: string;
  colour: string;
  /** Projects: their health colour; items: their own status colour. */
  status: StatusColour;
  /** 0..1 or null; drawn as the arc around a project hub. */
  progress: number | null;
  projectId: string;
  branch?: BranchKind;
  /** Cluster nodes: how many items they stand for. */
  count?: number;
  suggested?: boolean;
  /** Relative hub size, from how much the project holds. */
  weight: number;
}

export interface MapLink {
  source: string;
  target: string;
}

export interface ProjectMap {
  nodes: MapNode[];
  links: MapLink[];
  /** Projects that matched but were left off the map to keep it readable. */
  hiddenProjects: number;
}

export function clusterId(projectId: string, branch: BranchKind): string {
  return `cluster:${projectId}:${branch}`;
}

/** Every branch of a thread, depth first, the trunk's own order kept. */
export function flattenThread(thread: ProjectThread | undefined): ThreadBranch[] {
  const out: ThreadBranch[] = [];
  const walk = (list: ThreadBranch[]) => {
    for (const b of list) {
      out.push(b);
      walk(b.children);
    }
  };
  if (thread) walk(thread.branches);
  return out;
}

/**
 * Projects as hubs; the opened project's items around it, grouped by branch
 * kind, newest first, with each kind's overflow folded into one cluster.
 */
export function buildProjectMap(
  projects: Project[],
  opened?: { id: string; thread?: ProjectThread },
): ProjectMap {
  const shown = projects.slice(0, MAX_MAP_PROJECTS);
  if (opened && !shown.some((p) => p.id === opened.id)) {
    const extra = projects.find((p) => p.id === opened.id);
    if (extra) shown.push(extra);
  }
  const nodes: MapNode[] = shown.map((p) => ({
    id: p.id,
    type: "project",
    label: p.title,
    colour: HEALTH_HEX[p.health.colour],
    status: p.health.colour,
    progress: p.health.progress,
    projectId: p.id,
    weight: Math.min(3, 1 + Math.log10(1 + p.counts.conversations + p.health.total + p.counts.decisions)),
  }));
  const links: MapLink[] = [];
  if (opened?.thread && shown.some((p) => p.id === opened.id)) {
    const byKind = new Map<BranchKind, ThreadBranch[]>();
    for (const b of flattenThread(opened.thread)) {
      const list = byKind.get(b.branch) ?? [];
      list.push(b);
      byKind.set(b.branch, list);
    }
    for (const kind of BRANCH_KINDS) {
      const list = (byKind.get(kind) ?? []).sort((a, b) => b.last_activity_ms - a.last_activity_ms);
      for (const item of list.slice(0, MAX_PER_BRANCH)) {
        nodes.push({
          id: item.id,
          type: "item",
          label: item.title,
          colour: HEALTH_HEX[item.status],
          status: item.status,
          progress: null,
          projectId: opened.id,
          branch: kind,
          suggested: item.assignment === "suggested",
          weight: 1,
        });
        links.push({ source: item.id, target: opened.id });
      }
      const rest = list.length - MAX_PER_BRANCH;
      if (rest > 0) {
        const id = clusterId(opened.id, kind);
        nodes.push({
          id,
          type: "cluster",
          label: `+${rest}`,
          colour: "#7dd3fc",
          status: "blue",
          progress: null,
          projectId: opened.id,
          branch: kind,
          count: rest,
          weight: 1.4,
        });
        links.push({ source: id, target: opened.id });
      }
    }
  }
  return { nodes, links, hiddenProjects: Math.max(0, projects.length - MAX_MAP_PROJECTS) };
}

/** Branch kinds present in a thread, in vocabulary order, with their counts. */
export function branchKindCounts(thread: ProjectThread | undefined): [BranchKind, number][] {
  if (!thread) return [];
  return BRANCH_KINDS.filter((k) => (thread.counts[k] ?? 0) > 0).map((k) => [k, thread.counts[k]]);
}

/**
 * The thread with only the chosen branch kinds kept. A parent of a kept
 * branch stays (so the path to it is visible) even when its own kind is off.
 */
export function filterThread(branches: ThreadBranch[], kinds: Set<BranchKind>): ThreadBranch[] {
  if (kinds.size === 0) return branches;
  const out: ThreadBranch[] = [];
  for (const b of branches) {
    const children = filterThread(b.children, kinds);
    if (kinds.has(b.branch) || children.length) out.push({ ...b, children });
  }
  return out;
}

/** The path from the trunk down to a branch, for the breadcrumb. */
export function pathTo(branches: ThreadBranch[], id: string): ThreadBranch[] {
  for (const b of branches) {
    if (b.id === id) return [b];
    const below = pathTo(b.children, id);
    if (below.length) return [b, ...below];
  }
  return [];
}
