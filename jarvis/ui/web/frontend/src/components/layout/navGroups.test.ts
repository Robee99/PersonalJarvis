import { describe, expect, it } from "vitest";
import { Gauge } from "lucide-react";

import {
  ADVANCED_NAV_ITEMS,
  HUD_SECTION_IDS,
  NAV_GROUPS,
  PRIMARY_DESTINATIONS,
  destinationFor,
  isHudSection,
  presentNavItem,
} from "@/components/layout/navGroups";

const chats = NAV_GROUPS.flat().find((i) => i.id === "chats")!;
const agents = NAV_GROUPS.flat().find((i) => i.id === "agents")!;

describe("presentNavItem", () => {
  it("names the front page \"HUD\" in either mode — voice mode is a state of the chat inside it", () => {
    for (const surface of ["voice", "chat"] as const) {
      const row = presentNavItem(chats, surface);
      expect(row.labelKey).toBe("nav.hud");
      expect(row.icon).toBe(Gauge);
    }
  });

  it("keeps the section id so the row still lands on the front page", () => {
    expect(presentNavItem(chats, "voice").id).toBe("chats");
    expect(presentNavItem(chats, "chat").id).toBe("chats");
  });

  it("passes every other row through untouched", () => {
    expect(presentNavItem(agents, "voice")).toBe(agents);
    expect(presentNavItem(agents, "chat")).toBe(agents);
  });
});

describe("the HUD is one section (chat + HUD + memory orb)", () => {
  it("has ONE sidebar row for chat, HUD and orb, first in the list, lit for all three ids", () => {
    const rows = NAV_GROUPS.flat();
    expect(rows[0].id).toBe("chats");
    expect(rows[0].matchIds).toEqual(["chats", "deck", "orb"]);
    expect(rows.filter((r) => r.id === "deck" || r.id === "orb")).toEqual([]);
  });

  it("keeps the old ids resolving to it", () => {
    expect(HUD_SECTION_IDS).toEqual(["chats", "deck", "orb"]);
    for (const id of HUD_SECTION_IDS) {
      expect(isHudSection(id)).toBe(true);
      expect(destinationFor(id)?.key).toBe("chat");
    }
    expect(isHudSection("memory")).toBe(false);
  });

  it("is the first primary destination and the only one naming the chat or the orb", () => {
    expect(PRIMARY_DESTINATIONS[0].target).toBe("chats");
    expect(PRIMARY_DESTINATIONS[0].labelKey).toBe("nav5.hud");
    expect(PRIMARY_DESTINATIONS.map((d) => d.key)).toEqual(["chat", "agents", "work", "settings"]);
    expect(PRIMARY_DESTINATIONS.filter((d) => d.sections.some(isHudSection))).toHaveLength(1);
    expect(ADVANCED_NAV_ITEMS.some((item) => isHudSection(item.id))).toBe(false);
  });
});

describe("the tool catalogs stay out of the main navigation", () => {
  it("does not expose the broad app, skill, MCP, or marketplace catalogs", () => {
    const ids = NAV_GROUPS.flat().map((item) => item.id);
    expect(ids).not.toContain("armory");
    expect(ids).not.toContain("plugins");
    expect(ids).not.toContain("marketplace");
  });

  it("does not put tool catalogs in the Agents destination tabs", () => {
    const agentsDestination = PRIMARY_DESTINATIONS.find((item) => item.key === "agents");
    expect(agentsDestination?.tabs?.map((tab) => tab.id)).toEqual(["agents", "clis"]);
    expect(agentsDestination?.sections).not.toContain("armory");
    expect(agentsDestination?.sections).not.toContain("marketplace");
  });
});
