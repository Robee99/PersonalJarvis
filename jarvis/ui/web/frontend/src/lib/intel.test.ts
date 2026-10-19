import { describe, expect, it } from "vitest";
import { anglesFor, projectPoint } from "@/components/stark/OrbitGlobe";
import { DEFAULT_FILTERS, forGlobe, inRegion, matches, safeHref, type IntelEvent } from "@/lib/intel";
import { useIntelStore } from "@/store/intel";

const NOW = 1_792_400_400_000;

function event(patch: Partial<IntelEvent>): IntelEvent {
  return {
    id: "t:1",
    source: "t",
    kind: "earthquake",
    category: "hazard",
    title: "x",
    summary: null,
    evidence: "measured",
    automated: false,
    occurred_at: NOW - 3_600_000,
    updated_at: null,
    lat: 10,
    lon: 10,
    place: null,
    country: null,
    severity: null,
    severity_label: null,
    links: [],
    attrs: {},
    ingested_at: null,
    related: [],
    stale: false,
    ...patch,
  };
}

describe("intel filters", () => {
  const oceania = { id: "oceania", name: "Oceania", boxes: [[-50, 110, 0, 180], [-50, -180, 0, -130]] as [number, number, number, number][] };

  it("matches time, severity and region like the backend", () => {
    expect(matches(event({}), DEFAULT_FILTERS, [], NOW)).toBe(true);
    expect(matches(event({ occurred_at: NOW - 30 * 3_600_000 }), DEFAULT_FILTERS, [], NOW)).toBe(false);
    expect(matches(event({ severity: null }), { ...DEFAULT_FILTERS, minSeverity: 1 }, [], NOW)).toBe(false);
    const f = { ...DEFAULT_FILTERS, region: "oceania" };
    expect(matches(event({ lat: -12.7, lon: -163.4 }), f, [oceania], NOW)).toBe(true);
    expect(matches(event({ lat: null, lon: null }), f, [oceania], NOW)).toBe(false);
    expect(inRegion(oceania, 40, 2)).toBe(false);
  });

  it("puts only placed incidents on a globe, most severe first, within budget", () => {
    const out = forGlobe([event({ id: "a", severity: 1 }), event({ id: "b", lat: null }), event({ id: "c", severity: 4 })], 1);
    expect(out.map((e) => e.id)).toEqual(["c"]);
  });

  it("opens only http(s) links", () => {
    expect(safeHref("https://example.org/a")).toBe("https://example.org/a");
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref("not a url")).toBeNull();
  });
});

describe("intel store commands", () => {
  it("ignores malformed commands and unknown values", () => {
    const store = useIntelStore.getState();
    store.applyCommand(null);
    store.applyCommand({ action: "explode" });
    store.applyCommand({ action: "filter", category: "aliens", evidence: "media,rumour", hours: -5, min_severity: 9 });
    expect(useIntelStore.getState().filters).toEqual({ ...DEFAULT_FILTERS, hours: null, evidence: ["media"], minSeverity: 4 });
    store.applyCommand({ action: "reset" });
    expect(useIntelStore.getState().filters).toEqual(DEFAULT_FILTERS);
  });
});

describe("built-in globe picking", () => {
  it("projects the point the globe faces to the middle, and the far side as hidden", () => {
    const { phi, theta } = anglesFor(28.6, 77.2);
    const p = projectPoint(28.6, 77.2, phi, theta, 1, 0.015);
    expect(p.visible).toBe(true);
    expect(p.x).toBeCloseTo(0.5, 5);
    expect(p.y).toBeCloseTo(0.5, 5);
    const far = projectPoint(-28.6, 77.2 - 180, phi, theta, 1, 0.015);
    expect(far.visible).toBe(false);
  });
});
