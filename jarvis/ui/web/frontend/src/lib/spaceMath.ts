/**
 * The small amount of astronomy the realistic Earth needs, kept free of
 * three.js so it can be tested on its own.
 *
 * Everything is in an Earth-fixed frame with the Earth's radius as 1: +Y is
 * the North Pole, +X points at latitude 0 / longitude 0, and longitude grows
 * towards −Z. That is the frame three.js's `SphereGeometry` puts an
 * equirectangular map in, so a day map lines up without any extra turn.
 */
import { gmst } from "@/lib/orbit";

export const EARTH_RADIUS_KM = 6371;

const DEG = Math.PI / 180;

export type Vec3 = [number, number, number];

/** A point at a latitude, longitude (degrees) and height above the ground (km). */
export function latLonToVec(lat: number, lon: number, altKm = 0): Vec3 {
  const r = 1 + altKm / EARTH_RADIUS_KM;
  const la = lat * DEG;
  const lo = lon * DEG;
  return [r * Math.cos(la) * Math.cos(lo), r * Math.sin(la), -r * Math.cos(la) * Math.sin(lo)];
}

/** The latitude and longitude (degrees) a direction from the centre points at. */
export function vecToLatLon([x, y, z]: Vec3): { lat: number; lon: number } {
  const r = Math.hypot(x, y, z) || 1;
  return { lat: Math.asin(y / r) / DEG, lon: Math.atan2(-z, x) / DEG };
}

/**
 * Where the Sun is overhead at a Unix time (ms): the low-precision solar
 * position from the Astronomical Almanac, good to about a hundredth of a
 * degree in declination, far finer than a terminator drawn on screen needs.
 */
export function subsolarPoint(ms: number): { lat: number; lon: number } {
  const d = ms / 86_400_000 + 2_440_587.5 - 2_451_545.0;
  const meanLon = 280.46 + 0.9856474 * d;
  const g = (357.528 + 0.9856003 * d) * DEG;
  const eclLon = (meanLon + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * DEG;
  const obliquity = (23.439 - 0.0000004 * d) * DEG;
  const dec = Math.asin(Math.sin(obliquity) * Math.sin(eclLon));
  const ra = Math.atan2(Math.cos(obliquity) * Math.sin(eclLon), Math.cos(eclLon));
  let lon = (ra - gmst(ms)) / DEG;
  lon = ((((lon + 180) % 360) + 360) % 360) - 180;
  return { lat: dec / DEG, lon };
}

/** The unit vector towards the Sun, in the Earth-fixed frame, at `ms`. */
export function sunDirection(ms: number): Vec3 {
  const p = subsolarPoint(ms);
  return latLonToVec(p.lat, p.lon, 0);
}

/** Whether a ground point is in daylight at `ms` (the Sun above its horizon). */
export function isDaylight(lat: number, lon: number, ms: number): boolean {
  const [sx, sy, sz] = sunDirection(ms);
  const [x, y, z] = latLonToVec(lat, lon, 0);
  return sx * x + sy * y + sz * z > 0;
}

/** The two looks the Orbital screen's globe has, and where the choice is kept. */
export type OrbitalLook = "realistic" | "hud";
export const ORBITAL_VIEW_KEY = "orbital.view";

export function readOrbitalLook(): OrbitalLook {
  try {
    return window.localStorage.getItem(ORBITAL_VIEW_KEY) === "hud" ? "hud" : "realistic";
  } catch {
    // Storage can be blocked (private window, locked-down WebView): use the default.
    return "realistic";
  }
}

export function writeOrbitalLook(look: OrbitalLook): void {
  try {
    window.localStorage.setItem(ORBITAL_VIEW_KEY, look);
  } catch {
    // Storage can be blocked; the choice then lasts until the screen closes.
  }
}

/**
 * Whether this page can draw WebGL at all. The probe's context is handed back
 * at once so it does not spend one of the page's few live contexts.
 */
export function canDrawWebgl(): boolean {
  try {
    const canvas = document.createElement("canvas");
    const gl = (canvas.getContext("webgl2") ?? canvas.getContext("webgl")) as WebGLRenderingContext | null;
    if (!gl) return false;
    (gl.getExtension("WEBGL_lose_context") as { loseContext?: () => void } | null)?.loseContext?.();
    return true;
  } catch {
    // A renderer that throws on the probe cannot draw the realistic Earth either.
    return false;
  }
}
