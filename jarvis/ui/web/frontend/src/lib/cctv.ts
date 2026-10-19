import { useQuery } from "@tanstack/react-query";

/**
 * Public road and city cameras as God's Eye View lists them
 * (`/api/intel/cctv`, from `jarvis/intel/cctv.py`). The list comes through
 * JARVIS; each frame loads straight from the local God's Eye View server,
 * which fetches it from the camera's operator.
 */
export interface CctvCamera {
  id: string;
  name: string;
  city: string;
  city_id: string;
  provider: string;
  lat: number;
  lon: number;
  /** "video" cameras stream inside God's Eye View; the wall shows "image" ones. */
  feed: "image" | "video";
  heading: number | null;
}

export interface CctvCity {
  id: string;
  name: string;
  count: number;
  video: number;
  lat: number;
  lon: number;
  providers: string[];
}

export interface CctvCatalog {
  available: boolean;
  /** "gev_down" when God's Eye View did not answer; "not_loopback" for a remote setting. */
  reason: string | null;
  gev: string | null;
  cameras: CctvCamera[];
  cities: CctvCity[];
  fetched_at: number;
}

export interface CctvHealth {
  ok: number;
  degraded: number;
  error: number;
  unknown: number;
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as T;
}

export function useCctvCatalog(enabled = true) {
  return useQuery<CctvCatalog>({
    queryKey: ["intel", "cctv"],
    queryFn: () => getJson<CctvCatalog>("/api/intel/cctv"),
    enabled,
    // When God's Eye View was down, look again soon; otherwise the list holds.
    refetchInterval: (q) => (q.state.data && !q.state.data.available ? 30_000 : 10 * 60_000),
    staleTime: 60_000,
    retry: 1,
  });
}

export function useCctvHealth(enabled = true) {
  return useQuery<CctvHealth>({
    queryKey: ["intel", "cctv", "health"],
    queryFn: () => getJson<CctvHealth>("/api/intel/cctv/health"),
    enabled,
    refetchInterval: 30_000,
    retry: 0,
  });
}

/** The latest frame of a camera; `tick` makes the image load again. */
export function frameUrl(gev: string, id: string, tick: number): string {
  return `${gev}/api/cctv/frame/${encodeURIComponent(id)}?t=${tick}`;
}

/** How often a camera wall loads new frames, and the one camera shown large. */
export const WALL_REFRESH_MS = 15_000;
export const FOCUS_REFRESH_MS = 4_000;
