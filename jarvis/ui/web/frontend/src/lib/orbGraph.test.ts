import { describe, expect, it } from "vitest";

import {
  buildOrbGraph,
  connectedApps,
  createRegionForce,
  filterOrbGraph,
  itemsFromHermes,
  itemsFromJarvis,
  matchOrbNodes,
  regionAnchors,
  type OrbSources,
} from "@/lib/orbGraph";

const jarvis = itemsFromJarvis({
  skills: [
    { name: "cli-gcloud", category: "meta", description: "Drive\nGoogle Cloud", state: "validated" },
    { name: "notes", category: "memory", state: "invalid" },
    { name: "notes", category: "memory", state: "invalid" },
  ],
  tools: [{ name: "click", description: "Click", risk_tier: "ask" }],
  mcp: [{ name: "fs", display: "Files", status: "running" }],
});

const sources: OrbSources = {
  wiki: {
    nodes: [
      { id: "user", kind: "entity", title: "User" },
      { id: "log", kind: "meta", title: "Log" },
    ],
    edges: [
      { source: "log", target: "user" },
      { source: "user", target: "log" },
      { source: "log", target: "missing" },
    ],
  },
  ...jarvis,
  apps: connectedApps([
    { id: "github", display_name: "GitHub", category: "Developer", status: "connected" },
    { id: "notion", display_name: "Notion", category: "Knowledge", status: "not_connected" },
  ]),
};

describe("buildOrbGraph", () => {
  it("draws only real things and real links: no hubs, no spokes", () => {
    const graph = buildOrbGraph(sources);
    expect(graph.nodes.some((n) => n.id.startsWith("hub:") || n.id === "core")).toBe(false);
    expect(graph.links).toEqual([{ source: "wiki:log", target: "wiki:user" }]);
  });

  it("counts each real thing once, after duplicates are dropped", () => {
    const graph = buildOrbGraph(sources);
    expect(graph.counts).toEqual({ wiki: 1, concepts: 1, skills: 2, tools: 1, apps: 1, mcp: 1 });
    expect(graph.nodes).toHaveLength(7);
  });

  it("shows only connected apps, not the marketplace", () => {
    const ids = buildOrbGraph(sources).nodes.map((n) => n.id);
    expect(ids).toContain("apps:github");
    expect(ids).not.toContain("apps:notion");
  });

  it("marks a catalog that could not be read as unavailable, and an empty one as zero", () => {
    const graph = buildOrbGraph({ tools: [] });
    expect(graph.counts.tools).toBe(0);
    expect(graph.unavailable).toEqual(["wiki", "concepts", "skills", "apps", "mcp"]);
  });

  it("knows what is switched off", () => {
    const byId = new Map(buildOrbGraph(sources).nodes.map((n) => [n.id, n]));
    expect(byId.get("skills:notes")?.live).toBe(false);
    expect(byId.get("mcp:fs")?.live).toBe(true);
    expect(byId.get("skills:cli-gcloud")?.detail).toBe("Meta · Drive Google Cloud");
    expect(byId.get("wiki:user")?.slug).toBe("user");
  });
});

describe("itemsFromHermes", () => {
  it("takes skills, toolsets and MCP servers from Hermes's inventory", () => {
    const items = itemsFromHermes({
      available: true,
      skills: { items: [{ name: "notes", provenance: "bundled", enabled: false }] },
      mcp_servers: [{ name: "jarvis", transport: "http", enabled: true }],
      toolsets: { available: true, items: [{ name: "web", label: "Web", enabled: true, tool_count: 3 }] },
    });
    const graph = buildOrbGraph({ wiki: { nodes: [], edges: [] }, apps: [], ...items, capabilitySource: "hermes" });
    expect(graph.capabilitySource).toBe("hermes");
    expect(graph.counts).toMatchObject({ skills: 1, tools: 1, mcp: 1 });
    expect(graph.nodes.find((n) => n.id === "skills:notes")?.live).toBe(false);
    expect(graph.nodes.find((n) => n.id === "tools:web")?.detail).toBe("Toolset on for Jarvis · 3 tools");
    expect(graph.unavailable).toEqual([]);
  });

  it("reports Hermes toolsets as unavailable when Hermes couldn't list them", () => {
    const items = itemsFromHermes({
      available: true,
      skills: { items: [] },
      mcp_servers: [],
      toolsets: { available: false },
    });
    expect(items.tools).toBeUndefined();
  });
});

describe("filterOrbGraph and matchOrbNodes", () => {
  it("hides a family with its links and keeps the totals", () => {
    const full = buildOrbGraph(sources);
    const graph = filterOrbGraph(full, new Set(["wiki"]));
    expect(graph.nodes.some((n) => n.group === "wiki")).toBe(false);
    expect(graph.nodes.some((n) => n.group === "concepts")).toBe(true);
    expect(graph.links).toEqual([]);
    expect(graph.counts).toEqual(full.counts);
  });

  it("ranks label prefixes first and searches only what is shown", () => {
    const full = buildOrbGraph(sources);
    expect(matchOrbNodes(full, "google").map((n) => n.id)).toEqual(["skills:cli-gcloud"]);
    expect(matchOrbNodes(full, "  ")).toEqual([]);
    const hidden = filterOrbGraph(full, new Set(["skills"]));
    expect(matchOrbNodes(hidden, "google")).toEqual([]);
    const ranked = matchOrbNodes(
      { nodes: [
        { id: "a", label: "my notes", group: "wiki", live: true, detail: "" },
        { id: "b", label: "notes", group: "wiki", live: true, detail: "" },
        { id: "c", label: "x", group: "wiki", live: true, detail: "about notes" },
      ] },
      "notes",
    );
    expect(ranked.map((n) => n.id)).toEqual(["b", "a", "c"]);
  });
});

describe("regions", () => {
  it("puts each family on its own spot and pulls nodes toward it", () => {
    const anchors = regionAnchors(["wiki", "skills"], 100);
    expect(anchors.get("wiki")).not.toEqual(anchors.get("skills"));
    const node = { group: "skills" as const, x: 0, y: 0, vx: 0, vy: 0 };
    const force = createRegionForce(anchors, 0.1);
    force.initialize([node]);
    force(1);
    const target = anchors.get("skills")!;
    expect(Math.sign(node.vy)).toBe(Math.sign(target.y));
  });
});
