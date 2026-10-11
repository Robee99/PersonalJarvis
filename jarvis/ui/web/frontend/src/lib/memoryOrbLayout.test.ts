/**
 * The HUD memory orb's geometry and voice matching, pinned without WebGL:
 * the same tree always lands in the same place, each level on its shell,
 * the steppers wrap, and only a real, specific name in a sentence moves
 * the focus.
 */
import { describe, expect, it } from "vitest";

import {
  INNER_RADIUS,
  OUTER_RADIUS,
  capLeaves,
  fibonacciSphere,
  layoutOrb,
  nodeNamedIn,
  stepCommunity,
  stepLeaf,
  type OrbCommunity,
  type OrbLeaf,
  type OrbTree,
  type Vec3,
} from "./memoryOrbLayout";

const community = (
  id: string,
  title: string,
  extra: Partial<OrbCommunity> = {},
): OrbCommunity => ({
  id,
  title,
  level: 1,
  parent: null,
  kind: "project",
  colour: "blue",
  reason: "",
  progress: null,
  ref: { type: "project", id },
  size: 3,
  ...extra,
});

const leaf = (id: string, title: string, communityId: string, extra: Partial<OrbLeaf> = {}): OrbLeaf => ({
  id,
  title,
  kind: "TASK",
  colour: "yellow",
  community: communityId,
  ref: { type: "entity", id },
  ...extra,
});

const TREE: Pick<OrbTree, "communities" | "leaves"> = {
  communities: [
    community("p-atlas", "Atlas launch", { colour: "red" }),
    community("area-concepts", "Concepts", { kind: "area", colour: "reference", ref: null }),
    community("c-orbit", "Orbital mechanics", {
      level: 2,
      parent: "area-concepts",
      kind: "notes",
      colour: "reference",
      ref: null,
    }),
    community("c-loose", "Loose notes", {
      level: 2,
      parent: "area-concepts",
      kind: "notes",
      colour: "reference",
      ref: null,
    }),
    community("p-forge", "Forge", { colour: "green" }),
  ],
  leaves: [
    leaf("t-1", "Wire telemetry", "p-atlas"),
    leaf("b-1", "Thruster vendor late", "p-atlas", { kind: "BLOCKER", colour: "red" }),
    leaf("t-2", "Paint the fairing", "p-atlas"),
    leaf("n-hohmann", "Hohmann transfer", "c-orbit", { kind: "note", colour: "reference", ref: { type: "note", slug: "hohmann" } }),
    leaf("n-notes", "Notes", "c-loose", { kind: "note", colour: "reference", ref: { type: "note", slug: "notes" } }),
    leaf("n-ops", "Ops", "c-loose", { kind: "note", colour: "reference", ref: { type: "note", slug: "ops" } }),
  ],
};

const length = (v: Vec3) => Math.hypot(v[0], v[1], v[2]);

describe("layoutOrb", () => {
  it("is deterministic: the same tree lands in the same place", () => {
    const a = layoutOrb(TREE);
    const b = layoutOrb(TREE);
    expect(a.nodes.map((n) => [n.id, n.pos])).toEqual(b.nodes.map((n) => [n.id, n.pos]));
    expect(a.communityOrder).toEqual(b.communityOrder);
  });

  it("puts every level on its own shell", () => {
    const layout = layoutOrb(TREE);
    for (const node of layout.nodes) {
      const r = length(node.pos);
      if (node.level === 1) expect(r).toBeCloseTo(INNER_RADIUS, 6);
      if (node.level === 2) expect(r).toBeCloseTo(INNER_RADIUS * 1.12, 6);
      if (node.level === 3) expect(r).toBeCloseTo(OUTER_RADIUS, 6);
    }
    expect(layout.nodes).toHaveLength(TREE.communities.length + TREE.leaves.length);
  });

  it("links each node to its parent and walks communities top level first", () => {
    const layout = layoutOrb(TREE);
    expect(layout.byId.get("p-atlas")?.parent).toBeNull();
    expect(layout.byId.get("c-orbit")?.parent).toBe("area-concepts");
    expect(layout.byId.get("t-1")?.parent).toBe("p-atlas");
    expect(layout.communityOrder).toEqual(["p-atlas", "area-concepts", "c-orbit", "c-loose", "p-forge"]);
    expect(layout.leavesOf.get("p-atlas")?.map((n) => n.id)).toEqual(["t-1", "b-1", "t-2"]);
  });

  it("draws an empty tree as nothing", () => {
    const layout = layoutOrb({ communities: [], leaves: [] });
    expect(layout.nodes).toEqual([]);
    expect(layout.communityOrder).toEqual([]);
  });

  it("spreads the top level evenly over the unit sphere", () => {
    const dirs = fibonacciSphere(12);
    expect(dirs).toHaveLength(12);
    for (const d of dirs) expect(length(d)).toBeCloseTo(1, 6);
    expect(fibonacciSphere(0)).toEqual([]);
  });
});

describe("stepLeaf", () => {
  const layout = layoutOrb(TREE);

  it("walks a community's leaves and wraps around both ends", () => {
    expect(stepLeaf(layout, "t-1", 1)?.id).toBe("b-1");
    expect(stepLeaf(layout, "t-2", 1)?.id).toBe("t-1");
    expect(stepLeaf(layout, "t-1", -1)?.id).toBe("t-2");
  });

  it("enters a community at its first or last leaf", () => {
    expect(stepLeaf(layout, "p-atlas", 1)?.id).toBe("t-1");
    expect(stepLeaf(layout, "p-atlas", -1)?.id).toBe("t-2");
  });

  it("has nowhere to go from a leafless community or an unknown id", () => {
    expect(stepLeaf(layout, "p-forge", 1)).toBeNull();
    expect(stepLeaf(layout, "nope", 1)).toBeNull();
  });
});

describe("stepCommunity", () => {
  const layout = layoutOrb(TREE);

  it("starts at either end when nothing is focused", () => {
    expect(stepCommunity(layout, null, 1)?.id).toBe("p-atlas");
    expect(stepCommunity(layout, null, -1)?.id).toBe("p-forge");
  });

  it("wraps around and steps from a leaf's community", () => {
    expect(stepCommunity(layout, "p-forge", 1)?.id).toBe("p-atlas");
    expect(stepCommunity(layout, "p-atlas", -1)?.id).toBe("p-forge");
    expect(stepCommunity(layout, "t-2", 1)?.id).toBe("area-concepts");
  });

  it("returns nothing for an empty orb", () => {
    expect(stepCommunity(layoutOrb({ communities: [], leaves: [] }), null, 1)).toBeNull();
  });
});

describe("nodeNamedIn", () => {
  const layout = layoutOrb(TREE);

  it("finds a title said as whole words, case and punctuation aside", () => {
    expect(nodeNamedIn(layout, "What's blocking ATLAS LAUNCH?")?.id).toBe("p-atlas");
    expect(nodeNamedIn(layout, "explain the hohmann transfer, please")?.id).toBe("n-hohmann");
  });

  it("prefers the longest title named", () => {
    expect(nodeNamedIn(layout, "is the thruster vendor late for atlas launch")?.id).toBe("b-1");
  });

  it("does not match inside other words", () => {
    expect(nodeNamedIn(layout, "the forgery case")).toBeNull();
    expect(nodeNamedIn(layout, "reforge it")).toBeNull();
  });

  it("ignores short and generic titles", () => {
    expect(nodeNamedIn(layout, "open the notes")).toBeNull();
    expect(nodeNamedIn(layout, "call ops now")).toBeNull();
    expect(nodeNamedIn(layout, "ok")).toBeNull();
    expect(nodeNamedIn(layout, "")).toBeNull();
  });

  it("matches nothing on an empty orb", () => {
    expect(nodeNamedIn(layoutOrb({ communities: [], leaves: [] }), "atlas launch")).toBeNull();
  });
});

describe("capLeaves", () => {
  it("keeps everything under the cap", () => {
    expect(capLeaves(TREE.leaves, 600)).toBe(TREE.leaves);
  });

  it("shares the cap between communities and keeps the original order", () => {
    const many = [
      ...Array.from({ length: 50 }, (_, i) => leaf(`big-${i}`, `Big ${i}`, "big")),
      leaf("s-1", "Small one", "small"),
      leaf("s-2", "Small two", "small"),
    ];
    const kept = capLeaves(many, 10);
    expect(kept).toHaveLength(10);
    expect(kept.filter((l) => l.community === "small").map((l) => l.id)).toEqual(["s-1", "s-2"]);
    expect(kept.slice(0, 8).map((l) => l.id)).toEqual(Array.from({ length: 8 }, (_, i) => `big-${i}`));
  });
});
