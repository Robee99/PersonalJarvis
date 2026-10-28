/**
 * World incidents (`/api/intel/*`, `jarvis/intel`): the one list the World
 * panel, both globes and the agent tools share. An id means the same incident
 * everywhere, so selecting it in one place selects it in all of them.
 *
 * Every incident says what it rests on (`evidence`): an instrument
 * measurement, an agency alert, or a machine-coded media report. A media
 * report is never shown as a confirmed event.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";

export type Evidence = "measured" | "agency" | "media";
export type Category = "hazard" | "unrest";

export interface IntelLink {
  name: string;
  url: string;
}

export interface IntelEvent {
  id: string;
  source: string;
  kind: string;
  category: Category;
  title: string;
  summary: string | null;
  evidence: Evidence;
  automated: boolean;
  occurred_at: number;
  updated_at: number | null;
  lat: number | null;
  lon: number | null;
  place: string | null;
  country: string | null;
  /** 0 minor .. 4 extreme, only from a scale the source gave. */
  severity: number | null;
  severity_label: string | null;
  links: IntelLink[];
  attrs: Record<string, unknown>;
  ingested_at: number | null;
  related: string[];
  /** The source's newest refresh failed; this is its last known state. */
  stale: boolean;
}

export interface IntelSource {
  id: string;
  name: string;
  attribution: string;
  home_url: string;
  terms_url: string;
  status: "never" | "ok" | "stale" | "unavailable";
  fetched_at: number | null;
  count: number;
  error: string | null;
  refresh_s: number;
}

export interface IntelRegion {
  id: string;
  name: string;
  /** [south, west, north, east]; west > east crosses the antimeridian. */
  boxes: [number, number, number, number][];
}

export interface IntelSnapshot {
  rev: number;
  events: IntelEvent[];
  sources: IntelSource[];
  regions: IntelRegion[];
}

interface EventsBody extends Partial<IntelSnapshot> {
  rev: number;
  unchanged: boolean;
}

export const INTEL_POLL_MS = 60_000;
const KEY = ["intel", "events"] as const;

export const KINDS = [
  "earthquake",
  "cyclone",
  "storm",
  "flood",
  "volcano",
  "wildfire",
  "drought",
  "landslide",
  "ice",
  "dust",
  "protest",
  "violence",
  "other",
] as const;

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as T;
}

/**
 * The current incidents, polled while `enabled` (the World screen is open).
 * Each poll sends the revision it has; an unchanged answer keeps the same
 * object, so nothing downstream re-renders or re-sends markers.
 */
export function useIntelEvents(enabled = true) {
  const client = useQueryClient();
  return useQuery<IntelSnapshot>({
    queryKey: KEY,
    queryFn: async () => {
      const held = client.getQueryData<IntelSnapshot>(KEY);
      const body = await getJson<EventsBody>(
        held ? `/api/intel/events?rev=${held.rev}` : "/api/intel/events",
      );
      if (body.unchanged && held) return held;
      return {
        rev: body.rev,
        events: Array.isArray(body.events) ? body.events : [],
        sources: Array.isArray(body.sources) ? body.sources : [],
        regions: Array.isArray(body.regions) ? body.regions : [],
      };
    },
    enabled,
    refetchInterval: enabled ? INTEL_POLL_MS : false,
    staleTime: INTEL_POLL_MS / 2,
    retry: 1,
  });
}

export interface IntelFilters {
  /** Reported or updated in the last N hours; null = any time. */
  hours: number | null;
  kinds: string[];
  category: Category | null;
  region: string | null;
  minSeverity: number | null;
  evidence: Evidence[];
}

export const DEFAULT_FILTERS: IntelFilters = {
  hours: 24,
  kinds: [],
  category: null,
  region: null,
  minSeverity: null,
  evidence: [],
};

export function inRegion(region: IntelRegion, lat: number, lon: number): boolean {
  return region.boxes.some(([south, west, north, east]) => {
    if (lat < south || lat > north) return false;
    return west <= east ? lon >= west && lon <= east : lon >= west || lon <= east;
  });
}

export function lastSeen(event: IntelEvent): number {
  return event.updated_at ?? event.occurred_at;
}

/** The same rules as `jarvis/intel/query.py`. */
export function matches(
  event: IntelEvent,
  filters: IntelFilters,
  regions: IntelRegion[],
  now: number,
): boolean {
  if (filters.hours != null && lastSeen(event) < now - filters.hours * 3_600_000) return false;
  if (filters.kinds.length && !filters.kinds.includes(event.kind)) return false;
  if (filters.category && event.category !== filters.category) return false;
  if (filters.minSeverity != null && (event.severity == null || event.severity < filters.minSeverity))
    return false;
  if (filters.evidence.length && !filters.evidence.includes(event.evidence)) return false;
  if (filters.region) {
    const region = regions.find((r) => r.id === filters.region);
    if (!region || event.lat == null || event.lon == null) return false;
    if (!inRegion(region, event.lat, event.lon)) return false;
  }
  return true;
}

export type IntelSort = "severity" | "newest";

export function sortEvents(events: IntelEvent[], sort: IntelSort): IntelEvent[] {
  const out = [...events];
  out.sort((a, b) =>
    sort === "severity"
      ? (b.severity ?? -1) - (a.severity ?? -1) || lastSeen(b) - lastSeen(a)
      : lastSeen(b) - lastSeen(a),
  );
  return out;
}

export function placed(event: IntelEvent): event is IntelEvent & { lat: number; lon: number } {
  return event.lat != null && event.lon != null;
}

/** The most a globe draws at once: the most severe, then the newest. */
export const MARKER_BUDGET = 800;

export function forGlobe(events: IntelEvent[], budget = MARKER_BUDGET) {
  return sortEvents(events.filter(placed), "severity").slice(0, budget) as (IntelEvent & {
    lat: number;
    lon: number;
  })[];
}

/** Only http(s) links are opened; anything else is not a link. */
export function safeHref(url: string): string | null {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null; // not a URL at all: shown as text, never as a link
  }
}
