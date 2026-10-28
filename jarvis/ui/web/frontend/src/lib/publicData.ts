/**
 * The HUD's public data: free, keyless feeds served through the backend's
 * `/api/public/*` proxy (`jarvis/ui/web/public_data_routes.py`), which caches
 * each source within its terms and says when an answer is old or missing.
 *
 * Every body carries `available` (the source answered at least once),
 * `stale` (this is the last good answer; the newest refresh failed),
 * `fetched_at` (unix seconds) and the source's name and page for attribution.
 */
import { useQuery } from "@tanstack/react-query";

export interface SourceMeta {
  available: boolean;
  stale: boolean;
  fetched_at: number | null;
  source: string;
  source_url: string;
}

export interface Quake {
  id: string;
  mag: number | null;
  place: string;
  time_ms: number;
  lon: number;
  lat: number;
  depth_km: number | null;
  url: string | null;
  alert: string | null;
  tsunami: boolean | null;
  sig: number | null;
}

export interface NaturalEvent {
  id: string;
  title: string;
  category: string;
  category_id: string;
  lon: number;
  lat: number;
  date_ms: number;
  link: string | null;
  magnitude: number | null;
  unit: string | null;
}

export interface KpReading {
  time_ms: number;
  kp: number;
}

export interface Neo {
  des: string;
  time_ms: number;
  dist_au: number;
  dist_km: number;
  dist_ld: number;
  v_kms: number | null;
  h: number | null;
}

export interface SpaceBody extends SourceMeta {
  kp: { value: number; time_ms: number; series: KpReading[] } | null;
  f107: { value: number; time_ms: number } | null;
  neos: Neo[];
  failed: string[];
}

export interface IssBody extends SourceMeta {
  lat?: number;
  lon?: number;
  alt_km?: number;
  vel_kmh?: number;
  visibility?: string;
  time_ms?: number;
}

export interface Station {
  name: string;
  norad_id: number;
  epoch_ms: number;
  mean_motion: number;
  eccentricity: number;
  inclination: number;
  raan: number;
  arg_perigee: number;
  mean_anomaly: number;
  period_min: number;
  apogee_km: number;
  perigee_km: number;
}

export interface ListBody<T> extends SourceMeta {
  items: T[];
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as T;
}

/** One poll per source, at the source's own pace (the backend caches anyway). */
function usePublic<T>(name: string, refetchMs: number) {
  return useQuery<T>({
    queryKey: ["public", name],
    queryFn: () => getJson<T>(`/api/public/${name}`),
    refetchInterval: refetchMs,
    staleTime: Math.min(refetchMs, 60_000),
    retry: 1,
  });
}

export const useQuakes = () => usePublic<ListBody<Quake>>("quakes", 5 * 60_000);
export const useNaturalEvents = () => usePublic<ListBody<NaturalEvent>>("events", 15 * 60_000);
export const useSpace = () => usePublic<SpaceBody>("space", 15 * 60_000);
export const useStations = () => usePublic<ListBody<Station>>("stations", 30 * 60_000);
export const useIss = () => usePublic<IssBody>("iss", 5_000);

/** How a source stands: "live", "stale" (an old answer), "down" or "loading". */
export type FeedState = "live" | "stale" | "down" | "loading";

export function feedState(q: { data?: SourceMeta; isError: boolean; isPending: boolean }): FeedState {
  if (q.data) {
    if (!q.data.available) return "down";
    return q.data.stale ? "stale" : "live";
  }
  return q.isError ? "down" : "loading";
}

/** "now", "12m", "3h", "2d" since a millisecond timestamp. */
export function agoMs(ms: number | null | undefined, now = Date.now()): string {
  if (ms == null) return "—";
  const s = Math.max(0, (now - ms) / 1000);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86_400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86_400)}d`;
}

/** The broad regions the Intelligence screen counts events in. */
export const REGIONS = [
  "middle_east",
  "europe",
  "africa",
  "north_america",
  "south_america",
  "asia",
  "indo_pacific",
] as const;
export type Region = (typeof REGIONS)[number];

/**
 * Which broad region a coordinate falls in — rough boxes, checked in order,
 * good enough to say where activity clusters. Open ocean between them counts
 * as Indo-Pacific when it is in the Pacific or Indian Ocean, else null.
 */
export function regionOf(lon: number, lat: number): Region | null {
  const within = (la0: number, la1: number, lo0: number, lo1: number) =>
    lat >= la0 && lat <= la1 && lon >= lo0 && lon <= lo1;
  if (within(12, 42, 34, 63)) return "middle_east";
  if (within(35, 72, -25, 45)) return "europe";
  if (within(-36, 37, -20, 52)) return "africa";
  if (within(7, 84, -170, -50)) return "north_america";
  if (within(-56, 13, -92, -30)) return "south_america";
  if (within(5, 80, 45, 150)) return "asia";
  if (lon >= 60 || lon <= -150 || within(-60, 7, -150, -92)) return "indo_pacific";
  return null;
}

export type Severity = "high" | "med" | "low";

/** A quake's severity: USGS's own alert level first, then the magnitude. */
export function quakeSeverity(q: Pick<Quake, "mag" | "alert">): Severity {
  if (q.alert === "red" || q.alert === "orange" || (q.mag ?? 0) >= 6) return "high";
  if (q.alert === "yellow" || (q.mag ?? 0) >= 5) return "med";
  return "low";
}

/** A natural event's severity from its kind and, for storms, its wind. */
export function eventSeverity(e: Pick<NaturalEvent, "category_id" | "magnitude" | "unit">): Severity {
  if (e.category_id === "severeStorms") {
    if (e.unit === "kts" && (e.magnitude ?? 0) >= 64) return "high";
    return "med";
  }
  if (e.category_id === "volcanoes") return "med";
  return "low";
}

/** Planetary Kp as NOAA's words: quiet, unsettled, active, or a G1–G5 storm. */
export function kpLevel(kp: number): { key: string; storm: string | null } {
  if (kp >= 5) return { key: "storm", storm: `G${Math.min(5, Math.floor(kp) - 4)}` };
  if (kp >= 4) return { key: "active", storm: null };
  if (kp >= 3) return { key: "unsettled", storm: null };
  return { key: "quiet", storm: null };
}
