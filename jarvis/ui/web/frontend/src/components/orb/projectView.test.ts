import { describe, expect, it } from "vitest";

import type { Entity, Project, ProjectThread } from "./brainMap";
import {
  MAX_MAP_PROJECTS,
  MAX_PER_BRANCH,
  buildProjectMap,
  clusterId,
  colourCounts,
  filterProjects,
  filterThread,
  pathTo,
  projectNotes,
} from "./projectView";
import { branch, project } from "./testFixtures";

const NO_FILTER = { query: "", colours: new Set<Project["health"]["colour"]>(), sort: "priority" as const };

describe("project list shaping", () => {
  const list = [
    project("Orb redesign", { colour: "green", progress: 1, last_activity_ms: 5 }),
    project("API keys", { colour: "red", progress: 0.1, last_activity_ms: 1, summary: "Blocked on the vendor" }),
    project("Café site", { colour: "yellow", progress: 0.5, last_activity_ms: 9 }),
    project("Notes", { colour: "blue", progress: null, last_activity_ms: 3 }),
  ];

  it("puts what needs attention first by default (health, then priority)", () => {
    expect(filterProjects(list, NO_FILTER).map((p) => p.health.colour)).toEqual(["red", "yellow", "blue", "green"]);
  });

  it("sorts by recency, progress (untracked last) and name", () => {
    expect(filterProjects(list, { ...NO_FILTER, sort: "recent" }).map((p) => p.id)[0]).toBe("Café site");
    expect(filterProjects(list, { ...NO_FILTER, sort: "progress" }).map((p) => p.id)).toEqual([
      "Orb redesign",
      "Café site",
      "API keys",
      "Notes",
    ]);
    expect(filterProjects(list, { ...NO_FILTER, sort: "name" }).map((p) => p.id)[0]).toBe("API keys");
  });

  it("searches title, summary and reason, every word, ignoring case and accents", () => {
    expect(filterProjects(list, { ...NO_FILTER, query: "cafe" }).map((p) => p.id)).toEqual(["Café site"]);
    expect(filterProjects(list, { ...NO_FILTER, query: "vendor blocked" }).map((p) => p.id)).toEqual(["API keys"]);
    expect(filterProjects(list, { ...NO_FILTER, query: "vendor green" })).toEqual([]);
  });

  it("filters by health colour and counts every colour", () => {
    const colours = new Set<Project["health"]["colour"]>(["red", "green"]);
    expect(filterProjects(list, { ...NO_FILTER, colours }).map((p) => p.id)).toEqual(["API keys", "Orb redesign"]);
    expect(colourCounts(list)).toEqual({ red: 1, yellow: 1, blue: 1, green: 1 });
  });
});

describe("the project map", () => {
  it("draws hubs coloured by health and caps them, counting the rest", () => {
    const many = Array.from({ length: MAX_MAP_PROJECTS + 7 }, (_, i) => project(`p${i}`, { colour: "red" }));
    const map = buildProjectMap(many);
    expect(map.nodes).toHaveLength(MAX_MAP_PROJECTS);
    expect(map.hiddenProjects).toBe(7);
    expect(map.nodes[0]).toMatchObject({ type: "project", status: "red", colour: "#ff5d6c" });
  });

  it("keeps an opened project on the map even beyond the cap", () => {
    const many = Array.from({ length: MAX_MAP_PROJECTS + 2 }, (_, i) => project(`p${i}`));
    const map = buildProjectMap(many, { id: `p${MAX_MAP_PROJECTS + 1}` });
    expect(map.nodes.some((n) => n.id === `p${MAX_MAP_PROJECTS + 1}`)).toBe(true);
  });

  it("spreads an opened project's items by kind and folds each kind's overflow", () => {
    const decisions = Array.from({ length: MAX_PER_BRANCH + 3 }, (_, i) => branch(`d${i}`, "decision", [], i));
    const thread: ProjectThread = {
      project_id: "P",
      branches: [branch("chat", "topic", decisions, 100)],
      hidden_branches: 0,
      counts: { topic: 1, decision: decisions.length, experiment: 0, subtask: 0, blocker: 0, milestone: 0, artifact: 0 },
    };
    const map = buildProjectMap([project("P")], { id: "P", thread });
    const items = map.nodes.filter((n) => n.type === "item");
    expect(items.filter((n) => n.branch === "decision")).toHaveLength(MAX_PER_BRANCH);
    // Newest decisions are the ones drawn.
    expect(items.some((n) => n.id === `d${MAX_PER_BRANCH + 2}`)).toBe(true);
    expect(items.some((n) => n.id === "d0")).toBe(false);
    const cluster = map.nodes.find((n) => n.id === clusterId("P", "decision"));
    expect(cluster).toMatchObject({ type: "cluster", count: 3, label: "+3" });
    expect(map.links.every((l) => l.target === "P")).toBe(true);
  });
});

describe("thread helpers", () => {
  const tree = [
    branch("chat-a", "topic", [branch("dec-1", "decision"), branch("todo-1", "subtask")]),
    branch("run", "experiment", [branch("out", "artifact")]),
  ];

  it("finds the path from the trunk to a branch for the breadcrumb", () => {
    expect(pathTo(tree, "todo-1").map((b) => b.id)).toEqual(["chat-a", "todo-1"]);
    expect(pathTo(tree, "nope")).toEqual([]);
  });

  it("filters by branch kind and keeps the parents on the way", () => {
    const only = filterThread(tree, new Set(["decision"]));
    expect(only.map((b) => b.id)).toEqual(["chat-a"]);
    expect(only[0].children.map((b) => b.id)).toEqual(["dec-1"]);
    expect(filterThread(tree, new Set())).toBe(tree);
  });
});

describe("project notes", () => {
  const item = (id: string, locator: string, last: number, title = id): Entity => ({
    id,
    kind: "CONVERSATION",
    title,
    status: "blue",
    status_reason: "",
    confidence: 1,
    origin: "native",
    last_activity_ms: last,
    last_updated_ms: last,
    priority_score: 0,
    priority_reason: "",
    assignment: "confirmed",
    source: { source_kind: "wiki_import", source_id: id, conversation_id: id, api: null, locator },
  });

  it("lists each vault page once, newest first, and skips items with no page", () => {
    const notes = projectNotes([
      item("a", "imports/ai/conversations-001/Trip plan.md", 5, "Trip plan"),
      item("b", "imports\\ai\\conversations-002\\Budget.MD", 9, "Budget"),
      item("c", "imports/ai/conversations-001/Trip plan.md", 7, "Trip plan, later"),
      item("d", "", 20),
      item("e", "missions/m-1.json", 30),
    ]);
    expect(notes.map((n) => [n.slug, n.title, n.entity_id])).toEqual([
      ["Budget", "Budget", "b"],
      ["Trip plan", "Trip plan, later", "c"],
    ]);
    expect(notes[0].locator).toBe("imports/ai/conversations-002/Budget.MD");
  });
});
