import { describe, expect, it } from "vitest";

import { buildOrbGraph, filterOrbGraph, matchOrbNodes } from "@/lib/orbGraph";

const sources = {
  wiki: {
    nodes: [
      { id: "user", kind: "entity", title: "User" },
      { id: "log", kind: "meta", title: "Log" },
    ],
    edges: [
      { source: "log", target: "user" },
      { source: "log", target: "missing" },
    ],
  },
  skills: [
    { name: "cli-gcloud", category: "meta", description: "Drive\nGoogle Cloud", state: "validated" },
    { name: "notes", category: "memory", state: "invalid" },
  ],
  tools: [{ name: "click", description: "Click", risk_tier: "ask" }],
  apps: [
    { id: "github", display_name: "GitHub", category: "Developer", status: "connected" },
    { id: "notion", display_name: "Notion", category: "Knowledge", status: "not_connected" },
  ],
  mcp: [{ name: "fs", display: "Files", status: "running" }],
};

describe("buildOrbGraph", () => {
  it("hangs every family off the core and counts its leaves", () => {
    const graph = buildOrbGraph(sources);
    expect(graph.counts).toEqual({ wiki: 1, concepts: 1, skills: 2, tools: 1, apps: 2, mcp: 1 });
    for (const hub of ["hub:wiki", "hub:skills", "hub:tools", "hub:apps", "hub:mcp"]) {
      expect(graph.links).toContainEqual({ source: "core", target: hub });
    }
    expect(graph.links).toContainEqual({ source: "skills:cat:meta", target: "skill:cli-gcloud" });
    expect(graph.links).toContainEqual({ source: "tools:tier:ask", target: "tool:click" });
  });

  it("keeps wiki links between pages that exist and drops dangling ones", () => {
    const graph = buildOrbGraph(sources);
    expect(graph.links).toContainEqual({ source: "wiki:log", target: "wiki:user" });
    expect(graph.links.some((l) => l.target === "wiki:missing")).toBe(false);
  });

  it("marks only live things bright", () => {
    const byId = new Map(buildOrbGraph(sources).nodes.map((n) => [n.id, n]));
    expect(byId.get("app:github")?.live).toBe(true);
    expect(byId.get("app:notion")?.live).toBe(false);
    expect(byId.get("skill:notes")?.live).toBe(false);
    expect(byId.get("mcp:fs")?.live).toBe(true);
    expect(byId.get("skill:cli-gcloud")?.detail).toBe("Drive Google Cloud");
  });

  it("leaves out a family whose catalog is empty", () => {
    const graph = buildOrbGraph({ tools: [] });
    expect(graph.nodes.map((n) => n.id)).toEqual(["core"]);
  });
});

describe("filterOrbGraph and matchOrbNodes", () => {
  it("hides a family together with its links", () => {
    const graph = filterOrbGraph(buildOrbGraph(sources), new Set(["apps"]));
    expect(graph.nodes.some((n) => n.group === "apps")).toBe(false);
    expect(graph.links.some((l) => l.target.startsWith("app"))).toBe(false);
  });

  it("finds nodes by label or detail, ignoring case", () => {
    const hits = matchOrbNodes(buildOrbGraph(sources), "google");
    expect(hits.has("skill:cli-gcloud")).toBe(true);
    expect(matchOrbNodes(buildOrbGraph(sources), "  ").size).toBe(0);
  });
});
