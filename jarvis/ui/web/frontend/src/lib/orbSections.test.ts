import { describe, expect, it } from "vitest";

import type { OrbNode, OrbNoteGroups } from "@/lib/orbGraph";
import {
  SECTION_NEUTRAL,
  areaOf,
  buildSections,
  hashString,
  hslToHex,
  HUE_SLOTS,
  memoryColour,
  sectionFrameIds,
  sectionHues,
  stepSection,
} from "@/lib/orbSections";

const note = (slug: string, path?: string, extra: Partial<OrbNode> = {}): OrbNode => ({
  id: `wiki:${slug}`,
  label: slug,
  group: "wiki",
  live: true,
  detail: "",
  slug,
  path,
  kind: "session",
  ...extra,
});

const skill = (id: string): OrbNode => ({ id: `skills:${id}`, label: id, group: "skills", live: true, detail: "" });

describe("colours", () => {
  it("are stable, valid hex, and never the neutral of families and sections", () => {
    expect(hashString("abc")).toBe(hashString("abc"));
    expect(hashString("abc")).not.toBe(hashString("abd"));
    expect(hslToHex(0, 1, 0.5)).toBe("#ff0000");
    expect(hslToHex(120, 1, 0.25)).toBe("#008000");
    expect(hslToHex(-120, 1, 0.5)).toBe(hslToHex(240, 1, 0.5));
    const c = memoryColour(200, "wiki:a");
    expect(c).toMatch(/^#[0-9a-f]{6}$/);
    expect(c).toBe(memoryColour(200, "wiki:a"));
    expect(c).not.toBe(SECTION_NEUTRAL);
  });

  it("gives notes of one section different shades of one hue", () => {
    const colours = new Set(["a", "b", "c", "d", "e", "f"].map((id) => memoryColour(120, `wiki:${id}`)));
    expect(colours.size).toBeGreaterThan(3);
  });

  it("gives up to twelve sections twelve different hues, the same ones every time", () => {
    const ids = Array.from({ length: HUE_SLOTS }, (_, i) => `section:wiki|area:a${i}`);
    const hues = sectionHues(ids);
    const slots = new Set([...hues.values()].map((h) => Math.round(h / (360 / HUE_SLOTS)) % HUE_SLOTS));
    expect(slots.size).toBe(HUE_SLOTS);
    expect(sectionHues(ids)).toEqual(hues);
    // A section keeps its hue when a smaller one is added after it.
    expect(sectionHues([...ids.slice(0, 3), "section:wiki|area:new"]).get(ids[0])).toBe(hues.get(ids[0]));
    // Past twelve, hues are shared rather than invented.
    expect(sectionHues([...ids, "section:wiki|area:extra"]).size).toBe(HUE_SLOTS + 1);
  });
});

describe("areaOf", () => {
  it("mirrors the orb tree's vault areas", () => {
    expect(areaOf("sessions/chat-1.md", "session")).toEqual({ id: "area:sessions", title: "Sessions" });
    expect(areaOf("imports/ai-data/x/chat.md", "note")).toEqual({
      id: "area:imports/ai-data",
      title: "Import · ai-data",
    });
    expect(areaOf("Home_Lab/rack.md", "note")).toEqual({ id: "area:home_lab", title: "Home Lab" });
    expect(areaOf("loose.md", "note")).toEqual({ id: "area:note", title: "Note" });
    expect(areaOf(undefined, undefined)).toEqual({ id: "area:notes", title: "Notes" });
  });
});

describe("buildSections", () => {
  it("splits notes by vault area, keeps other families whole, and colours every memory", () => {
    const nodes = [
      note("s1", "sessions/s1.md"),
      note("s2", "sessions/s2.md"),
      note("s3", "sessions/s3.md"),
      note("i1", "imports/old/i1.md"),
      skill("x"),
      skill("y"),
    ];
    const sections = buildSections(nodes);
    expect(sections.list.map((s) => [s.id, s.memberIds.length, s.branch])).toEqual([
      ["section:wiki|area:sessions", 3, true],
      ["section:wiki|area:imports/old", 1, true],
      ["section:skills|family", 2, false],
    ]);
    expect(sections.byId.get("section:skills|family")?.title).toBeNull();
    expect(sections.ofNode.get("wiki:i1")?.title).toBe("Import · old");
    for (const n of nodes) expect(sections.colourOf.get(n.id)).toMatch(/^#[0-9a-f]{6}$/);
    // Notes of one section differ from each other.
    const shades = new Set(["wiki:s1", "wiki:s2", "wiki:s3"].map((id) => sections.colourOf.get(id)));
    expect(shades.size).toBeGreaterThan(1);
  });

  it("follows the orb tree's communities, and falls back to the area for a note it does not list", () => {
    const groups: OrbNoteGroups = {
      communities: [
        { id: "area:imports/old", title: "Import · old", level: 1, parent: null },
        { id: "area:imports/old#docker", title: "docker · compose", level: 2, parent: "area:imports/old" },
      ],
      leaves: [
        { id: "note:a", community: "area:imports/old#docker" },
        { id: "note:b", community: "area:imports/old#docker" },
        { id: "entity:task-1", community: "project:p1" },
      ],
    };
    const sections = buildSections(
      [note("a", "imports/old/a.md"), note("b", "imports/old/b.md"), note("c", "imports/old/c.md")],
      groups,
    );
    const docker = sections.ofNode.get("wiki:a")!;
    expect(docker.id).toBe("section:wiki|area:imports/old#docker");
    expect(docker.title).toBe("docker · compose");
    expect(docker.within).toBe("Import · old");
    expect(sections.ofNode.get("wiki:b")).toBe(docker);
    expect(sections.ofNode.get("wiki:c")?.id).toBe("section:wiki|area:imports/old");
  });
});

describe("stepSection", () => {
  const sections = buildSections([
    note("a", "one/a.md"),
    note("b", "one/b.md"),
    note("c", "two/c.md"),
    skill("x"),
  ]).list;

  it("walks forward and back, wrapping round", () => {
    const ids = sections.map((s) => s.id);
    expect(ids).toEqual(["section:wiki|area:one", "section:wiki|area:two", "section:skills|family"]);
    expect(stepSection(sections, null, 1)?.id).toBe(ids[0]);
    expect(stepSection(sections, null, -1)?.id).toBe(ids[2]);
    expect(stepSection(sections, ids[0], 1)?.id).toBe(ids[1]);
    expect(stepSection(sections, ids[2], 1)?.id).toBe(ids[0]);
    expect(stepSection(sections, ids[0], -1)?.id).toBe(ids[2]);
    expect(stepSection(sections, "section:gone", 1)?.id).toBe(ids[0]);
    expect(stepSection([], null, 1)).toBeNull();
  });

  it("frames a section's members and its branch point when it has one", () => {
    expect([...sectionFrameIds(sections[0])].sort()).toEqual(["section:wiki|area:one", "wiki:a", "wiki:b"]);
    expect([...sectionFrameIds(sections[2])]).toEqual(["skills:x"]);
  });
});
