/**
 * The memory orb's map: what the assistant remembers and what it can use.
 *
 * Only real things and real relationships are drawn. Notes are linked the way
 * the vault links them; nothing else gets an edge, because nothing else has
 * one. Families (notes, skills, tools, apps, MCP servers) are shown as regions
 * of the map, pulled together by `createRegionForce`, never as invented hub
 * nodes with spokes.
 *
 * Capabilities come from Hermes Agent when its inventory is readable (the
 * brain that actually uses them, and the same list the Tool Armory shows);
 * otherwise from Jarvis's own registries, and the legend says which.
 *
 * A catalog that could not be read is "unavailable", never zero.
 *
 * Pure: the view fetches the catalogs and hands them here, so the shape of the
 * map is testable without a canvas.
 */

export type OrbGroup = "wiki" | "concepts" | "skills" | "tools" | "apps" | "mcp";

export interface OrbNode {
  id: string;
  label: string;
  group: OrbGroup;
  /** On: an enabled skill or toolset, a connected app, an enabled server. */
  live: boolean;
  detail: string;
  /** Where "Open" leads. */
  section?: "memory" | "skills" | "plugins" | "mcps" | "jarvis-actions";
  /** The wiki slug, for notes: "Open" shows that page. */
  slug?: string;
}

export interface OrbLink {
  source: string;
  target: string;
}

export type CapabilitySource = "hermes" | "jarvis";

export interface OrbGraph {
  nodes: OrbNode[];
  links: OrbLink[];
  /** Real things per family, after duplicates are dropped. */
  counts: Record<OrbGroup, number>;
  /** Families whose catalog could not be read. */
  unavailable: OrbGroup[];
  capabilitySource: CapabilitySource;
}

/** One catalog entry, already reduced to what the map shows. */
export interface OrbItem {
  id: string;
  label: string;
  live: boolean;
  detail: string;
}

/** `undefined` = the catalog could not be read; `[]` = it is empty. */
export interface OrbSources {
  wiki?: {
    nodes: { id: string; kind: string; title?: string }[];
    edges: { source: string; target: string }[];
  };
  skills?: OrbItem[];
  tools?: OrbItem[];
  apps?: OrbItem[];
  mcp?: OrbItem[];
  capabilitySource?: CapabilitySource;
}

export const ORB_GROUP_COLOUR: Record<OrbGroup, string> = {
  wiki: "#5bd4a4",
  concepts: "#ffb84d",
  skills: "#b48cf2",
  tools: "#f47fa4",
  apps: "#6aa9ff",
  mcp: "#4fd1e8",
};

export const ORB_GROUP_LABEL: Record<OrbGroup, string> = {
  wiki: "Notes",
  concepts: "Concepts",
  skills: "Skills",
  tools: "Tools",
  apps: "Connected apps",
  mcp: "MCP servers",
};

export const ORB_GROUPS = Object.keys(ORB_GROUP_LABEL) as OrbGroup[];

/** Wiki kinds that read as ideas rather than notes get their own colour. */
const CONCEPT_KINDS = new Set(["concept", "project", "entity"]);

const RISK_LABEL: Record<string, string> = {
  safe: "Runs freely",
  monitor: "Runs and is logged",
  ask: "Asks before acting",
};

function oneLine(text: string | undefined, max = 140): string {
  const flat = (text ?? "").replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

function titleCase(text: string): string {
  return text.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function joined(...parts: (string | undefined)[]): string {
  return parts.filter((p) => p && p.trim()).join(" · ");
}

// ---------------------------------------------------------------------------
// Catalogs → items
// ---------------------------------------------------------------------------

export interface JarvisCatalogs {
  skills?: { name: string; category?: string; description?: string; state?: string }[];
  tools?: { name: string; description?: string; risk_tier?: string }[];
  mcp?: { name: string; display?: string; description?: string; status?: string }[];
}

export interface HermesCatalogs {
  available: boolean;
  skills: { items: { name: string; provenance?: string; enabled?: boolean }[] };
  mcp_servers: { name: string; transport?: string; enabled: boolean }[];
  toolsets: { available: boolean; items?: { name: string; label?: string; enabled: boolean; tool_count?: number }[] };
}

export type MarketplaceApp = {
  id: string;
  display_name?: string;
  description?: string;
  category?: string;
  status?: string;
};

/** Skills, toolsets and MCP servers as Hermes has them. */
export function itemsFromHermes(inv: HermesCatalogs): Pick<OrbSources, "skills" | "tools" | "mcp"> {
  return {
    skills: inv.skills.items.map((s) => ({
      id: s.name,
      label: s.name,
      live: s.enabled !== false,
      detail: joined(s.enabled === false ? "Disabled in Hermes" : "Enabled in Hermes", s.provenance && titleCase(s.provenance)),
    })),
    tools: inv.toolsets.available
      ? (inv.toolsets.items ?? []).map((t) => ({
          id: t.name,
          label: t.label || t.name,
          live: t.enabled,
          detail: joined(
            t.enabled ? "Toolset on for Jarvis" : "Toolset off for Jarvis",
            t.tool_count ? `${t.tool_count} tools` : undefined,
          ),
        }))
      : undefined,
    mcp: inv.mcp_servers.map((m) => ({
      id: m.name,
      label: m.name,
      live: m.enabled,
      detail: joined(m.enabled ? "Enabled in Hermes" : "Disabled in Hermes", m.transport),
    })),
  };
}

/** Skills, tools and MCP servers from Jarvis's own registries. */
export function itemsFromJarvis(cat: JarvisCatalogs): Pick<OrbSources, "skills" | "tools" | "mcp"> {
  return {
    skills: cat.skills?.map((s) => ({
      id: s.name,
      label: s.name,
      live: s.state !== "invalid" && s.state !== "disabled",
      detail: joined(s.category && titleCase(s.category), oneLine(s.description)),
    })),
    tools: cat.tools?.map((t) => ({
      id: t.name,
      label: t.name,
      live: true,
      detail: joined(RISK_LABEL[t.risk_tier ?? ""] ?? undefined, oneLine(t.description)),
    })),
    mcp: cat.mcp?.map((m) => ({
      id: m.name,
      label: m.display || m.name,
      live: m.status === "running",
      detail: joined(m.status === "running" ? "Running" : "Stopped", oneLine(m.description, 100)),
    })),
  };
}

/** Only the apps the user has actually connected; the marketplace is not memory. */
export function connectedApps(apps: MarketplaceApp[] | undefined): OrbItem[] | undefined {
  return apps
    ?.filter((a) => a.status === "connected")
    .map((a) => ({
      id: a.id,
      label: a.display_name || a.id,
      live: true,
      detail: joined(a.category, oneLine(a.description, 100)),
    }));
}

// ---------------------------------------------------------------------------
// The graph
// ---------------------------------------------------------------------------

const SECTION: Record<Exclude<OrbGroup, "wiki" | "concepts">, OrbNode["section"]> = {
  skills: "skills",
  tools: "jarvis-actions",
  apps: "plugins",
  mcp: "mcps",
};

export function buildOrbGraph(src: OrbSources): OrbGraph {
  const nodes: OrbNode[] = [];
  const links: OrbLink[] = [];
  const seen = new Set<string>();
  const counts: Record<OrbGroup, number> = { wiki: 0, concepts: 0, skills: 0, tools: 0, apps: 0, mcp: 0 };
  const unavailable: OrbGroup[] = [];

  const add = (node: OrbNode) => {
    if (seen.has(node.id)) return;
    seen.add(node.id);
    nodes.push(node);
    counts[node.group] += 1;
  };

  const wiki = src.wiki;
  if (!wiki || !Array.isArray(wiki.nodes)) {
    unavailable.push("wiki", "concepts");
  } else {
    for (const page of wiki.nodes) {
      add({
        id: `wiki:${page.id}`,
        label: page.title?.replace(/^"|"$/g, "") || page.id,
        group: CONCEPT_KINDS.has(page.kind) ? "concepts" : "wiki",
        live: true,
        detail: `${titleCase(page.kind)} page in memory`,
        section: "memory",
        slug: page.id,
      });
    }
    const linked = new Set<string>();
    for (const edge of wiki.edges ?? []) {
      const a = `wiki:${edge.source}`;
      const b = `wiki:${edge.target}`;
      const key = a < b ? `${a}|${b}` : `${b}|${a}`;
      if (a !== b && seen.has(a) && seen.has(b) && !linked.has(key)) {
        linked.add(key);
        links.push({ source: a, target: b });
      }
    }
  }

  for (const group of ["skills", "tools", "apps", "mcp"] as const) {
    const items = src[group];
    if (!items) {
      unavailable.push(group);
      continue;
    }
    for (const item of items) {
      add({
        id: `${group}:${item.id}`,
        label: item.label,
        group,
        live: item.live,
        detail: item.detail,
        section: SECTION[group],
      });
    }
  }

  return { nodes, links, counts, unavailable, capabilitySource: src.capabilitySource ?? "jarvis" };
}

/** The graph with the hidden families taken out, links included; counts stay the totals. */
export function filterOrbGraph(graph: OrbGraph, hidden: ReadonlySet<OrbGroup>): OrbGraph {
  if (!hidden.size) return graph;
  const keep = new Set(graph.nodes.filter((n) => !hidden.has(n.group)).map((n) => n.id));
  return {
    ...graph,
    nodes: graph.nodes.filter((n) => keep.has(n.id)),
    links: graph.links.filter((l) => keep.has(l.source) && keep.has(l.target)),
  };
}

/**
 * Nodes matching the search, best first: label prefix, then label, then
 * detail. Only what is on the map is searched, so a hit is always visible.
 */
export function matchOrbNodes(graph: Pick<OrbGraph, "nodes">, query: string): OrbNode[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const rank = (n: OrbNode): number => {
    const label = n.label.toLowerCase();
    if (label.startsWith(q)) return 0;
    if (label.includes(q)) return 1;
    return n.detail.toLowerCase().includes(q) ? 2 : -1;
  };
  return graph.nodes
    .map((n) => [rank(n), n] as const)
    .filter(([r]) => r >= 0)
    .sort((a, b) => a[0] - b[0] || a[1].label.localeCompare(b[1].label))
    .map(([, n]) => n);
}

// ---------------------------------------------------------------------------
// Layout regions
// ---------------------------------------------------------------------------

/** Where each family's region sits, on a ring around the origin. */
export function regionAnchors(groups: readonly OrbGroup[], radius: number): Map<OrbGroup, { x: number; y: number }> {
  const anchors = new Map<OrbGroup, { x: number; y: number }>();
  if (groups.length === 1) {
    anchors.set(groups[0], { x: 0, y: 0 });
    return anchors;
  }
  groups.forEach((group, i) => {
    const angle = (2 * Math.PI * i) / groups.length - Math.PI / 2;
    anchors.set(group, { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius });
  });
  return anchors;
}

interface RegionNode {
  group: OrbGroup;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
}

/** A d3-force-shaped pull of every node toward its family's anchor. */
export interface RegionForce {
  (alpha: number): void;
  initialize: (nodes: RegionNode[]) => void;
}

export function createRegionForce(
  anchors: ReadonlyMap<OrbGroup, { x: number; y: number }>,
  strength: number,
): RegionForce {
  let nodes: RegionNode[] = [];
  const force = ((alpha: number) => {
    const k = strength * alpha;
    for (const node of nodes) {
      const a = anchors.get(node.group);
      if (!a) continue;
      node.vx = (node.vx ?? 0) + (a.x - (node.x ?? 0)) * k;
      node.vy = (node.vy ?? 0) + (a.y - (node.y ?? 0)) * k;
    }
  }) as RegionForce;
  force.initialize = (next) => {
    nodes = next ?? [];
  };
  return force;
}
