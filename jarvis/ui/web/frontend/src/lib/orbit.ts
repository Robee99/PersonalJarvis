/**
 * Where a satellite is, from its published mean elements (CelesTrak GP data):
 * a two-body Kepler orbit with Earth's J2 drift of the node and perigee,
 * turned into latitude and longitude under the rotating Earth.
 *
 * This is an approximation for drawing — good to tens of kilometres over an
 * orbit or two from a fresh element set, drifting further with age (it leaves
 * out drag and the higher-order terms SGP4 models). The HUD labels paths
 * drawn from it as approximate; the ISS's live position comes from a
 * tracking service, not from here.
 */
import type { Station } from "@/lib/publicData";

const MU = 398_600.4418; // km³/s²
const RE = 6378.137; // km
const J2 = 1.08262668e-3;
const DEG = Math.PI / 180;

export interface GroundPoint {
  lat: number;
  lon: number;
  alt_km: number;
}

/** Greenwich mean sidereal time, radians, for a Unix time in ms. */
export function gmst(ms: number): number {
  const jd = ms / 86_400_000 + 2_440_587.5;
  const deg = 280.46061837 + 360.98564736629 * (jd - 2_451_545.0);
  return (((deg % 360) + 360) % 360) * DEG;
}

/** Eccentric anomaly from the mean anomaly (Newton, a few steps). */
function kepler(M: number, e: number): number {
  let E = e < 0.8 ? M : Math.PI;
  for (let i = 0; i < 12; i += 1) {
    const d = (E - e * Math.sin(E) - M) / (1 - e * Math.cos(E));
    E -= d;
    if (Math.abs(d) < 1e-10) break;
  }
  return E;
}

/** The sub-satellite point at `ms`. */
export function propagate(el: Station, ms: number): GroundPoint {
  const n = (el.mean_motion * 2 * Math.PI) / 86_400; // rad/s
  const a = Math.cbrt(MU / (n * n));
  const e = el.eccentricity;
  const i = el.inclination * DEG;
  const dt = (ms - el.epoch_ms) / 1000;
  const p = a * (1 - e * e);
  const k = 1.5 * n * J2 * (RE / p) ** 2;
  const raan = el.raan * DEG - k * Math.cos(i) * dt;
  const argp = el.arg_perigee * DEG + (k / 2) * (5 * Math.cos(i) ** 2 - 1) * dt;
  const M = (((el.mean_anomaly * DEG + n * dt) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
  const E = kepler(M, e);
  const nu = 2 * Math.atan2(Math.sqrt(1 + e) * Math.sin(E / 2), Math.sqrt(1 - e) * Math.cos(E / 2));
  const r = a * (1 - e * Math.cos(E));
  const u = argp + nu;
  // Perifocal → inertial (equatorial) frame.
  const x = r * (Math.cos(raan) * Math.cos(u) - Math.sin(raan) * Math.sin(u) * Math.cos(i));
  const y = r * (Math.sin(raan) * Math.cos(u) + Math.cos(raan) * Math.sin(u) * Math.cos(i));
  const z = r * Math.sin(u) * Math.sin(i);
  // Under the rotating Earth.
  const g = gmst(ms);
  const xe = x * Math.cos(g) + y * Math.sin(g);
  const ye = -x * Math.sin(g) + y * Math.cos(g);
  const lon = Math.atan2(ye, xe) / DEG;
  const lat = Math.atan2(z, Math.hypot(xe, ye)) / DEG;
  return { lat, lon, alt_km: r - RE };
}

/**
 * The ground track from `fromMs` for `minutes`, one point every `stepS`
 * seconds, split where it crosses the date line so a drawn line never
 * jumps across the map.
 */
export function groundTrack(el: Station, fromMs: number, minutes: number, stepS = 60): GroundPoint[][] {
  const runs: GroundPoint[][] = [[]];
  let prev: GroundPoint | null = null;
  for (let s = 0; s <= minutes * 60; s += stepS) {
    const pt = propagate(el, fromMs + s * 1000);
    if (prev && Math.abs(pt.lon - prev.lon) > 180) runs.push([]);
    runs[runs.length - 1].push(pt);
    prev = pt;
  }
  return runs.filter((run) => run.length > 1);
}

export interface Pass {
  /** When the satellite rises above the minimum elevation, ms. */
  start_ms: number;
  end_ms: number;
  /** Highest elevation over the pass, degrees, and when it is reached. */
  max_elev: number;
  max_ms: number;
}

/** A point on a spherical Earth, km, Earth-fixed. */
function ecef(lat: number, lon: number, r: number): [number, number, number] {
  const la = lat * DEG;
  const lo = lon * DEG;
  return [r * Math.cos(la) * Math.cos(lo), r * Math.cos(la) * Math.sin(lo), r * Math.sin(la)];
}

/** How high a satellite at `at` stands in an observer's sky, degrees. */
export function elevation(observer: { lat: number; lon: number }, at: GroundPoint): number {
  const o = ecef(observer.lat, observer.lon, RE);
  const s = ecef(at.lat, at.lon, RE + at.alt_km);
  const d = [s[0] - o[0], s[1] - o[1], s[2] - o[2]];
  const up = Math.hypot(o[0], o[1], o[2]);
  const len = Math.hypot(d[0], d[1], d[2]);
  return Math.asin((d[0] * o[0] + d[1] * o[1] + d[2] * o[2]) / (up * len)) / DEG;
}

/**
 * The satellite's passes over an observer from `fromMs` for `hours`, above
 * `minElev` degrees, found by stepping every `stepS` seconds — as rough as
 * `propagate` itself, so a pass time is good to about a minute.
 */
export function nextPasses(
  el: Station,
  observer: { lat: number; lon: number },
  fromMs: number,
  hours = 24,
  minElev = 10,
  stepS = 30,
): Pass[] {
  const passes: Pass[] = [];
  let open: Pass | null = null;
  for (let s = 0; s <= hours * 3600; s += stepS) {
    const ms = fromMs + s * 1000;
    const elev = elevation(observer, propagate(el, ms));
    if (elev >= minElev) {
      if (!open) open = { start_ms: ms, end_ms: ms, max_elev: elev, max_ms: ms };
      open.end_ms = ms;
      if (elev > open.max_elev) {
        open.max_elev = elev;
        open.max_ms = ms;
      }
    } else if (open) {
      passes.push(open);
      open = null;
    }
  }
  // A pass still overhead at the end of the window is left out: its end is unknown.
  return passes;
}
