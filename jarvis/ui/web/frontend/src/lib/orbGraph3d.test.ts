import { describe, expect, it } from "vitest";

import type { OrbNode } from "@/lib/orbGraph";
import { buildSceneGraph, coreId, familyAnchors, labelledHubs, sphereRadius } from "@/lib/orbGraph3d";

const note = (id: string, extra: Partial<OrbNode> = {}): OrbNode => ({
  id: `wiki:${id}`,
  label: id,
  group: "wiki",
  live: true,
  detail: "",
  slug: id,
  ...extra,
});

describe("buildSceneGraph", () => {
  it("adds one pinned core per family and a spoke from every member", () => {
    const nodes: OrbNode[] = [
      note("a"),
      note("b"),
      { id: "skill:x", label: "x", group: "skills", live: true, detail: "" },
    ];
    const scene = buildSceneGraph(nodes, [{ source: "wiki:a", target: "wiki:b" }], () => 0.5);
    const cores = scene.nodes.filter((n) => n.core);
    expect(cores.map((n) => n.id)).toEqual([coreId("wiki"), coreId("skills")]);
    for (const core of cores) expect(core.fx).toBe(core.x);
    const spokes = scene.links.filter((l) => l.kind === "family");
    expect(spokes).toHaveLength(3);
    expect(spokes).toContainEqual({ source: "skill:x", target: coreId("skills"), kind: "family" });
    expect(scene.links.filter((l) => l.kind === "note")).toEqual([
      { source: "wiki:a", target: "wiki:b", kind: "note" },
    ]);
  });

  it("links a community to the notes it was built from, once, and counts real links only", () => {
    const nodes: OrbNode[] = [
      note("a"),
      note("b"),
      {
        id: "community:1",
        label: "c",
        group: "communities",
        live: true,
        detail: "",
        sourceLinks: [
          { sourceId: "s1", sourcePath: "a.md", slug: "a" },
          { sourceId: "s2", sourcePath: "a.md", slug: "a" },
          { sourceId: "s3", sourcePath: "gone.md", slug: "gone" },
        ],
      },
    ];
    const scene = buildSceneGraph(nodes, [{ source: "wiki:a", target: "wiki:b" }], () => 0.5);
    expect(scene.links.filter((l) => l.kind === "source")).toEqual([
      { source: "community:1", target: "wiki:a", kind: "source" },
    ]);
    const a = scene.nodes.find((n) => n.id === "wiki:a")!;
    expect(a.degree).toBe(2);
    expect(sphereRadius(a)).toBeGreaterThan(sphereRadius({ core: false, degree: 0 }));
    expect(sphereRadius({ core: true, degree: 0 })).toBeGreaterThan(sphereRadius(a));
  });
});

describe("familyAnchors", () => {
  it("spreads families apart and centres a lone family", () => {
    expect(familyAnchors(["wiki"], 100).get("wiki")).toEqual({ x: 0, y: 0, z: 0 });
    const anchors = [...familyAnchors(["wiki", "skills", "tools", "apps"], 100).values()];
    for (let i = 0; i < anchors.length; i++) {
      for (let j = i + 1; j < anchors.length; j++) {
        const d = Math.hypot(anchors[i].x - anchors[j].x, anchors[i].y - anchors[j].y, anchors[i].z - anchors[j].z);
        expect(d).toBeGreaterThan(50);
      }
    }
  });
});

describe("labelledHubs", () => {
  it("names only the best-connected memories, never a core", () => {
    const nodes = Array.from({ length: 30 }, (_, i) => ({ id: `n${i}`, group: "wiki" as const, core: false, degree: i }));
    const hubs = labelledHubs([...nodes, { id: "family:wiki", group: "wiki", core: true, degree: 99 }]);
    expect(hubs.size).toBe(16);
    expect(hubs.has("n29")).toBe(true);
    expect(hubs.has("n13")).toBe(false);
    expect(hubs.has("family:wiki")).toBe(false);
  });
});
