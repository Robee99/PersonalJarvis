/**
 * The memory orb's map: everything the assistant knows and can reach, as one graph.
 *
 * The wiki map (`lib/wikiGraph.ts`) draws what the assistant remembers. The orb
 * adds what it can DO: its skills, its tools, the apps it is connected to and
 * the MCP servers it runs. Each family hangs off a hub, and the hubs hang off
 * the core, so a glance shows how big each part of the assistant is and a
 * filter hides a family without breaking the picture.
 *
 * Pure: the view fetches the five catalogs and hands them here, so the shape
 * of the map is testable without a canvas.
 */

export type OrbGroup = "core" | "wiki" | "concepts" | "skills" | "tools" | "apps" | "mcp";

export interface OrbNode {
  id: string;
  label: string;
  group: OrbGroup;
  /** A hub is a family or category node; leaves hang off it. */
  hub: boolean;
  /** Bright when the thing is live: a connected app, a running server. */
  live: boolean;
  detail: string;
  /** Where clicking the node leads, when there is a section for it. */
  section?: "memory" | "skills" | "plugins" | "mcps" | "jarvis-actions";
  /** The wiki slug, for wiki pages. */
  slug?: string;
}

export interface OrbLink {
  source: string;
  target: string;
}

export interface OrbGraph {
  nodes: OrbNode[];
  links: OrbLink[];
  counts: Record<Exclude<OrbGroup, "core">, number>;
}

export interface OrbSources {
  wiki?: {
    nodes: { id: string; kind: string; title?: string }[];
    edges: { source: string; target: string }[];
  };
  skills?: { name: string; category?: string; description?: string; state?: string }[];
  tools?: { name: string; description?: string; risk_tier?: string }[];
  apps?: { id: string; display_name?: string; description?: string; category?: string; status?: string }[];
  mcp?: { name: string; display?: string; description?: string; status?: string }[];
}

export const ORB_GROUP_COLOUR: Record<OrbGroup, string> = {
  core: "#f5f7ff",
  wiki: "#5bd4a4",
  concepts: "#ffb84d",
  skills: "#b48cf2",
  tools: "#f47fa4",
  apps: "#6aa9ff",
  mcp: "#4fd1e8",
};

export const ORB_GROUP_LABEL: Record<Exclude<OrbGroup, "core">, string> = {
  wiki: "Notes",
  concepts: "Concepts",
  skills: "Skills",
  tools: "Tools",
  apps: "Apps",
  mcp: "MCP servers",
};

export const ORB_GROUPS = Object.keys(ORB_GROUP_LABEL) as Exclude<OrbGroup, "core">[];

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

export function buildOrbGraph(src: OrbSources): OrbGraph {
  const nodes: OrbNode[] = [];
  const links: OrbLink[] = [];
  const seen = new Set<string>();
  const counts = { wiki: 0, concepts: 0, skills: 0, tools: 0, apps: 0, mcp: 0 };

  const add = (node: OrbNode, parent?: string) => {
    if (seen.has(node.id)) return;
    seen.add(node.id);
    nodes.push(node);
    if (parent) links.push({ source: parent, target: node.id });
  };
  const hub = (id: string, label: string, group: OrbGroup, parent: string, detail = "") =>
    add({ id, label, group, hub: true, live: true, detail }, parent);

  add({ id: "core", label: "Jarvis", group: "core", hub: true, live: true, detail: "The assistant" });

  // Memory: every wiki page, linked as the vault links them.
  const wiki = src.wiki;
  if (wiki && wiki.nodes.length) {
    hub("hub:wiki", "Memory", "wiki", "core", "What the assistant remembers");
    const wikiIds = new Set<string>();
    for (const page of wiki.nodes) {
      const group: OrbGroup = CONCEPT_KINDS.has(page.kind) ? "concepts" : "wiki";
      counts[group] += 1;
      wikiIds.add(`wiki:${page.id}`);
      add(
        {
          id: `wiki:${page.id}`,
          label: page.title?.replace(/^"|"$/g, "") || page.id,
          group,
          hub: false,
          live: true,
          detail: `${titleCase(page.kind)} page in the wiki`,
          section: "memory",
          slug: page.id,
        },
        "hub:wiki",
      );
    }
    for (const edge of wiki.edges) {
      const a = `wiki:${edge.source}`;
      const b = `wiki:${edge.target}`;
      if (a !== b && wikiIds.has(a) && wikiIds.has(b)) links.push({ source: a, target: b });
    }
  }

  // Skills, grouped by category.
  if (src.skills?.length) {
    hub("hub:skills", "Skills", "skills", "core", "Playbooks the assistant follows");
    for (const skill of src.skills) {
      const cat = skill.category || "other";
      const catId = `skills:cat:${cat}`;
      hub(catId, titleCase(cat), "skills", "hub:skills");
      counts.skills += 1;
      add(
        {
          id: `skill:${skill.name}`,
          label: skill.name,
          group: "skills",
          hub: false,
          live: skill.state !== "invalid" && skill.state !== "disabled",
          detail: oneLine(skill.description),
          section: "skills",
        },
        catId,
      );
    }
  }

  // Tools, grouped by how much they need the user's say-so.
  if (src.tools?.length) {
    hub("hub:tools", "Tools", "tools", "core", "Actions the assistant can take");
    for (const tool of src.tools) {
      const tier = tool.risk_tier || "monitor";
      const tierId = `tools:tier:${tier}`;
      hub(tierId, RISK_LABEL[tier] ?? titleCase(tier), "tools", "hub:tools");
      counts.tools += 1;
      add(
        {
          id: `tool:${tool.name}`,
          label: tool.name,
          group: "tools",
          hub: false,
          live: true,
          detail: oneLine(tool.description),
          section: "jarvis-actions",
        },
        tierId,
      );
    }
  }

  // Connected apps, grouped by category; the ones not yet connected stay dim.
  if (src.apps?.length) {
    hub("hub:apps", "Apps", "apps", "core", "Accounts the assistant can use");
    for (const app of src.apps) {
      const cat = app.category || "Other";
      const catId = `apps:cat:${cat}`;
      hub(catId, cat, "apps", "hub:apps");
      counts.apps += 1;
      const live = app.status === "connected";
      add(
        {
          id: `app:${app.id}`,
          label: app.display_name || app.id,
          group: "apps",
          hub: false,
          live,
          detail: `${live ? "Connected" : "Not connected"} · ${oneLine(app.description, 100)}`,
          section: "plugins",
        },
        catId,
      );
    }
  }

  // MCP servers.
  if (src.mcp?.length) {
    hub("hub:mcp", "MCP servers", "mcp", "core", "Tool servers the assistant runs");
    for (const server of src.mcp) {
      counts.mcp += 1;
      const live = server.status === "running";
      add(
        {
          id: `mcp:${server.name}`,
          label: server.display || server.name,
          group: "mcp",
          hub: false,
          live,
          detail: `${live ? "Running" : "Stopped"} · ${oneLine(server.description, 100)}`,
          section: "mcps",
        },
        "hub:mcp",
      );
    }
  }

  return { nodes, links, counts };
}

/** The graph with the hidden families taken out, links included. */
export function filterOrbGraph(graph: OrbGraph, hidden: ReadonlySet<OrbGroup>): OrbGraph {
  if (!hidden.size) return graph;
  const keep = new Set(graph.nodes.filter((n) => !hidden.has(n.group)).map((n) => n.id));
  return {
    nodes: graph.nodes.filter((n) => keep.has(n.id)),
    links: graph.links.filter((l) => keep.has(l.source) && keep.has(l.target)),
    counts: graph.counts,
  };
}

/** Case-insensitive label/detail match, for the search box. */
export function matchOrbNodes(graph: OrbGraph, query: string): Set<string> {
  const q = query.trim().toLowerCase();
  if (!q) return new Set();
  return new Set(
    graph.nodes
      .filter((n) => n.label.toLowerCase().includes(q) || n.detail.toLowerCase().includes(q))
      .map((n) => n.id),
  );
}
