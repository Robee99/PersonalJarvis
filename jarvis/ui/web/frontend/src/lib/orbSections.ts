/**
 * The memory map's sections and colours.
 *
 * A family (notes, concepts, skills, …) is split into sections, the chunks a
 * person jumps between. Notes and concept pages are put in the community the
 * vault's orb tree puts them in (`/api/wiki/orb-tree`: a vault area, or a link
 * or topic community inside a large one, such as one imported chat export);
 * a page the tree does not list falls back to its vault area, worked out from
 * its path the same way the tree does it (`area_of` in
 * `jarvis/memory/orb_tree.py`), so both land in the same section. Every other
 * family is one section.
 *
 * Families and sections are drawn neutral: they are labels, not data. The
 * colour belongs to each memory: a hue taken from its section, so a chunk
 * reads as one family of colour, shifted by the memory's own id, so no two
 * neighbours look the same. A section's hue is one of twelve, picked by a
 * hash of its id and moved on to the next free one when a larger section
 * already took it, so sections on screen differ while a memory mostly keeps
 * its colour from one visit to the next.
 *
 * Pure, so colours and navigation are testable without a canvas.
 */
import { ORB_GROUPS, type OrbGroup, type OrbNode, type OrbNoteGroups } from "@/lib/orbGraph";

export interface OrbSection {
  /** `section:<family>|<community or area>`; also the id of its branch point on the map. */
  id: string;
  group: OrbGroup;
  /** The section's name; null for a family that is one section (the view shows the family's name). */
  title: string | null;
  /** The vault area a link or topic community sits in. */
  within?: string;
  memberIds: string[];
  /** The hue every member's colour starts from, and the swatch the section list shows. */
  hue: number;
  colour: string;
  /** Drawn as a branch point between its family's core and its members (its family has several sections). */
  branch: boolean;
}

export interface OrbSections {
  /** In the order the section steppers walk them: family by family, largest section first. */
  list: OrbSection[];
  byId: Map<string, OrbSection>;
  /** The section each memory is in. */
  ofNode: Map<string, OrbSection>;
  /** Each memory's own colour. */
  colourOf: Map<string, string>;
}

/** Families and sections: one muted tone, readable on the map's dark backdrop. */
export const SECTION_NEUTRAL = "#8d99ab";
/** The names written on families and sections. */
export const SECTION_LABEL = "#c9d2de";

const NOTE_GROUPS = new Set<OrbGroup>(["wiki", "concepts"]);

/** FNV-1a: a small stable hash, so a colour never depends on load order. */
export function hashString(text: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** `#rrggbb` for a hue (degrees), saturation and lightness (0..1). */
export function hslToHex(h: number, s: number, l: number): string {
  const hue = ((h % 360) + 360) % 360;
  const a = s * Math.min(l, 1 - l);
  const channel = (n: number) => {
    const k = (n + hue / 30) % 12;
    const v = l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
    return Math.round(v * 255)
      .toString(16)
      .padStart(2, "0");
  };
  return `#${channel(0)}${channel(8)}${channel(4)}`;
}

/** How many hues sections are given, evenly round the wheel. */
export const HUE_SLOTS = 12;

/**
 * Each section's hue, for sections in the order they are listed: the slot its
 * id hashes to, or the next one no earlier section has taken. Past twelve
 * sections, slots are shared.
 */
export function sectionHues(sectionIds: readonly string[]): Map<string, number> {
  const taken = new Set<number>();
  const out = new Map<string, number>();
  for (const id of sectionIds) {
    const h = hashString(id);
    const preferred = h % HUE_SLOTS;
    let slot = preferred;
    for (let i = 0; i < HUE_SLOTS && taken.has(slot); i++) slot = (preferred + i + 1) % HUE_SLOTS;
    if (taken.size < HUE_SLOTS) taken.add(slot);
    else slot = preferred;
    out.set(id, slot * (360 / HUE_SLOTS) + ((h >>> 4) % 7) - 3);
  }
  return out;
}

/**
 * A memory's own colour: its section's hue, turned up to 9 degrees either
 * way and made lighter or deeper by its id. Saturated and mid-light, so it
 * stands out from the neutral families and sections on the dark backdrop.
 */
export function memoryColour(hue: number, nodeId: string): string {
  const h = hashString(nodeId);
  const saturation = 0.62 + ((h >>> 8) % 25) / 100;
  const lightness = 0.5 + ((h >>> 16) % 21) / 100;
  return hslToHex(hue + ((h % 19) - 9), saturation, lightness);
}

/** `(area id, area title)` for a page: its top folder, its import, or its kind. */
export function areaOf(path: string | undefined, kind: string | undefined): { id: string; title: string } {
  const parts = (path ?? "").replaceAll("\\", "/").split("/").slice(0, -1).filter(Boolean);
  if (parts.length === 0) {
    const k = kind || "notes";
    return { id: `area:${k}`, title: k.charAt(0).toUpperCase() + k.slice(1) };
  }
  if (parts[0].toLowerCase() === "imports" && parts.length > 1) {
    return { id: `area:imports/${parts[1]}`, title: `Import · ${parts[1]}` };
  }
  const words = parts[0].replace(/[-_]/g, " ").split(" ");
  return {
    id: `area:${parts[0].toLowerCase()}`,
    title: words.map((w) => (w ? w.charAt(0).toUpperCase() + w.slice(1).toLowerCase() : w)).join(" "),
  };
}

/** The memories of what is shown, put in sections and coloured. See the module docstring. */
export function buildSections(nodes: readonly OrbNode[], noteGroups?: OrbNoteGroups): OrbSections {
  const communities = new Map((noteGroups?.communities ?? []).map((c) => [c.id, c]));
  const communityOf = new Map<string, string>();
  for (const leaf of noteGroups?.leaves ?? []) {
    if (leaf.id.startsWith("note:")) communityOf.set(leaf.id.slice(5), leaf.community);
  }

  const found = new Map<string, OrbSection>();
  const ofNode = new Map<string, OrbSection>();
  for (const node of nodes) {
    let key = "family";
    let title: string | null = null;
    let within: string | undefined;
    if (NOTE_GROUPS.has(node.group)) {
      const community = node.slug ? communities.get(communityOf.get(node.slug) ?? "") : undefined;
      if (community) {
        key = community.id;
        title = community.title;
        within = community.parent ? communities.get(community.parent)?.title : undefined;
      } else {
        const area = areaOf(node.path, node.kind);
        key = area.id;
        title = area.title;
      }
    }
    const id = `section:${node.group}|${key}`;
    let section = found.get(id);
    if (!section) {
      section = { id, group: node.group, title, within, memberIds: [], hue: 0, colour: "", branch: false };
      found.set(id, section);
    }
    section.memberIds.push(node.id);
    ofNode.set(node.id, section);
  }

  const rank = (g: OrbGroup) => ORB_GROUPS.indexOf(g);
  const list = [...found.values()].sort(
    (a, b) =>
      rank(a.group) - rank(b.group) ||
      b.memberIds.length - a.memberIds.length ||
      (a.title ?? "").localeCompare(b.title ?? "") ||
      a.id.localeCompare(b.id),
  );
  const perFamily = new Map<OrbGroup, number>();
  for (const s of list) perFamily.set(s.group, (perFamily.get(s.group) ?? 0) + 1);
  // Hues go to the largest sections first, so they are the ones that keep theirs.
  const hues = sectionHues([...list].sort((a, b) => b.memberIds.length - a.memberIds.length || a.id.localeCompare(b.id)).map((s) => s.id));
  for (const s of list) {
    s.branch = (perFamily.get(s.group) ?? 0) > 1;
    s.hue = hues.get(s.id) ?? 0;
    s.colour = hslToHex(s.hue, 0.74, 0.6);
  }

  const colourOf = new Map<string, string>();
  for (const [nodeId, section] of ofNode) colourOf.set(nodeId, memoryColour(section.hue, nodeId));
  return { list, byId: new Map(list.map((s) => [s.id, s])), ofNode, colourOf };
}

/**
 * The next or previous section, wrapping round. With nothing current the
 * first step forward lands on the first section, the first step back on the
 * last.
 */
export function stepSection(list: readonly OrbSection[], currentId: string | null, delta: number): OrbSection | null {
  if (list.length === 0) return null;
  const at = currentId ? list.findIndex((s) => s.id === currentId) : -1;
  if (at < 0) return list[delta >= 0 ? 0 : list.length - 1];
  const n = list.length;
  return list[(((at + delta) % n) + n) % n];
}

/** What a jump to a section frames: its members and, when it has one, its branch point. */
export function sectionFrameIds(section: OrbSection): Set<string> {
  const ids = new Set(section.memberIds);
  if (section.branch) ids.add(section.id);
  return ids;
}
