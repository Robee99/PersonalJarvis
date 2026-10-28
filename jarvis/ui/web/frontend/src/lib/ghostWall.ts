import type { CctvCamera, CctvCatalog } from "@/lib/cctv";
import type { LiveStream } from "@/lib/live";

/**
 * The Ghost screen's camera wall, as data: every public camera JARVIS can
 * show (God's Eye View's road and city cameras, and the city cameras on
 * YouTube), placed in a country and a city, filtered, put in an order that
 * mixes places, and cut into the rows that are on screen. No React here, so
 * the rules are tested on their own.
 */

export type TileSize = "s" | "m" | "l";
/** The narrowest a tile may be, per size; the wall fills the width with as many as fit. */
export const TILE_MIN: Record<TileSize, number> = { s: 168, m: 248, l: 380 };
export const TILE_GAP = 4;
/** Frame tiles are 16:9 like most road cameras. */
export const TILE_RATIO = 9 / 16;

export interface GhostCam {
  /** The GEV camera id, or `yt:<key>` for a YouTube camera. */
  id: string;
  /** "frame": a still that loads again; "video": a YouTube live stream. */
  kind: "frame" | "video";
  name: string;
  /** What a YouTube camera looks at ("Shinjuku skyline"); "" for a road camera. */
  place: string;
  city: string;
  cityKey: string;
  country: string;
  countryCode: string;
  countryKey: string;
  provider: string;
  lat: number | null;
  lon: number | null;
  heading: number | null;
  camera?: CctvCamera;
  stream?: LiveStream;
}

export interface GhostCity {
  key: string;
  name: string;
  countryKey: string;
  count: number;
  video: number;
  lat: number | null;
  lon: number | null;
}

export interface GhostCountry {
  key: string;
  /** English name; "" when nobody said which country. */
  name: string;
  code: string;
  count: number;
  video: number;
  cities: GhostCity[];
}

export interface WallFilter {
  country: string | null;
  city: string | null;
  words: string;
}

/** Where each YouTube city camera stands (the live catalog names only the city). */
const YT_PLACES: Record<string, { country: string; code: string; lat: number; lon: number }> = {
  "jerusalem-western-wall": { country: "Israel", code: "IL", lat: 31.7767, lon: 35.2345 },
  "istanbul-galata": { country: "Türkiye", code: "TR", lat: 41.0256, lon: 28.9741 }, // i18n-allow: official country name
  "london-notting-hill": { country: "United Kingdom", code: "GB", lat: 51.5094, lon: -0.1967 },
  "london-heathrow": { country: "United Kingdom", code: "GB", lat: 51.47, lon: -0.4543 },
  "amsterdam-ijmuiden": { country: "Netherlands", code: "NL", lat: 52.4628, lon: 4.6 },
  "new-york-gwb": { country: "United States", code: "US", lat: 40.8517, lon: -73.9527 },
  "new-york-brooklyn": { country: "United States", code: "US", lat: 40.6602, lon: -73.969 },
  "niagara-falls": { country: "Canada", code: "CA", lat: 43.0896, lon: -79.0849 },
  miami: { country: "United States", code: "US", lat: 25.7743, lon: -80.1659 },
  "hong-kong-peak": { country: "Hong Kong", code: "HK", lat: 22.2711, lon: 114.15 },
  "tokyo-shinjuku": { country: "Japan", code: "JP", lat: 35.6938, lon: 139.7034 },
  "seoul-han-river": { country: "South Korea", code: "KR", lat: 37.5172, lon: 126.9909 },
  "taipei-tamsui": { country: "Taiwan", code: "TW", lat: 25.1689, lon: 121.4398 },
  "singapore-marina-bay": { country: "Singapore", code: "SG", lat: 1.2834, lon: 103.8607 },
  "tokyo-haneda": { country: "Japan", code: "JP", lat: 35.5494, lon: 139.7798 },
  iss: { country: "Space", code: "", lat: 0, lon: 0 },
};

const UNKNOWN_COUNTRY = "zz";

function countryKey(name: string, code: string): string {
  return code ? code.toLowerCase() : name ? name.toLowerCase() : UNKNOWN_COUNTRY;
}

function cityKeyOf(country: string, city: string): string {
  return `${country}/${city.trim().toLowerCase() || "other"}`;
}

/** Every camera the wall can show: GEV's image cameras and the YouTube city cameras. */
export function buildCams(data: CctvCatalog | undefined, streams: LiveStream[] = []): GhostCam[] {
  const out: GhostCam[] = [];
  const cityCountry = new Map<string, { name: string; code: string; lat: number; lon: number }>();
  for (const c of data?.cities ?? []) {
    if (c.country || c.country_code) cityCountry.set(c.name.toLowerCase(), { name: c.country ?? "", code: c.country_code ?? "", lat: c.lat, lon: c.lon });
  }
  for (const cam of data?.cameras ?? []) {
    // A "video" camera streams inside God's Eye View only; the wall shows frames.
    if (cam.feed !== "image") continue;
    const country = cam.country ?? "";
    const code = (cam.country_code ?? "").toUpperCase();
    const ck = countryKey(country, code);
    const city = cam.city || cam.city_id;
    out.push({
      id: cam.id,
      kind: "frame",
      name: cam.name,
      place: "",
      city,
      cityKey: cityKeyOf(ck, city),
      country,
      countryCode: code,
      countryKey: ck,
      provider: cam.provider,
      lat: cam.lat,
      lon: cam.lon,
      heading: cam.heading,
      camera: cam,
    });
  }
  for (const s of streams) {
    if (s.kind !== "camera") continue;
    const known = YT_PLACES[s.key];
    const byCity = known ? undefined : cityCountry.get(s.name.toLowerCase());
    const country = known?.country ?? byCity?.name ?? "";
    const code = known?.code ?? byCity?.code ?? "";
    const ck = countryKey(country, code);
    out.push({
      id: `yt:${s.key}`,
      kind: "video",
      name: s.place ? `${s.name} · ${s.place}` : s.name,
      place: s.place,
      city: s.name,
      cityKey: cityKeyOf(ck, s.name),
      country,
      countryCode: code,
      countryKey: ck,
      provider: "YouTube",
      lat: s.key === "iss" ? null : known?.lat ?? byCity?.lat ?? null,
      lon: s.key === "iss" ? null : known?.lon ?? byCity?.lon ?? null,
      heading: null,
      stream: s,
    });
  }
  return out;
}

/**
 * Countries, each with its cities, largest first. Cameras in `dead` (their
 * frame failed again and again) do not count, so a city with no camera that
 * shows a picture leaves the list.
 */
export function placesOf(cams: GhostCam[], dead: ReadonlySet<string> = new Set()): GhostCountry[] {
  const countries = new Map<string, GhostCountry & { byCity: Map<string, GhostCity & { latSum: number; lonSum: number; placed: number }> }>();
  for (const cam of cams) {
    if (dead.has(cam.id)) continue;
    let country = countries.get(cam.countryKey);
    if (!country) {
      country = { key: cam.countryKey, name: cam.country, code: cam.countryCode, count: 0, video: 0, cities: [], byCity: new Map() };
      countries.set(cam.countryKey, country);
    }
    country.count += 1;
    country.video += cam.kind === "video" ? 1 : 0;
    let city = country.byCity.get(cam.cityKey);
    if (!city) {
      city = { key: cam.cityKey, name: cam.city, countryKey: cam.countryKey, count: 0, video: 0, lat: null, lon: null, latSum: 0, lonSum: 0, placed: 0 };
      country.byCity.set(cam.cityKey, city);
    }
    city.count += 1;
    city.video += cam.kind === "video" ? 1 : 0;
    if (cam.lat !== null && cam.lon !== null) {
      city.latSum += cam.lat;
      city.lonSum += cam.lon;
      city.placed += 1;
    }
  }
  const bySize = <T extends { count: number; name: string }>(a: T, b: T) => b.count - a.count || a.name.localeCompare(b.name);
  return [...countries.values()]
    .map(({ byCity, ...country }) => ({
      ...country,
      cities: [...byCity.values()]
        .map(({ latSum, lonSum, placed, ...city }) => ({
          ...city,
          lat: placed ? latSum / placed : null,
          lon: placed ? lonSum / placed : null,
        }))
        .sort(bySize),
    }))
    .sort((a, b) => (a.key === UNKNOWN_COUNTRY ? 1 : b.key === UNKNOWN_COUNTRY ? -1 : bySize(a, b)));
}

/** Takes one from each list in turn until all are used up. */
export function interleave<T>(lists: T[][]): T[] {
  const out: T[] = [];
  const longest = Math.max(0, ...lists.map((l) => l.length));
  for (let i = 0; i < longest; i++) {
    for (const list of lists) if (i < list.length) out.push(list[i]);
  }
  return out;
}

/**
 * The wall's order: one camera from each country in turn and, inside a
 * country, one from each city in turn, so a wide view shows many places at
 * once rather than the first five hundred cameras of one road agency. A
 * city's YouTube cameras come before its road cameras.
 */
export function wallOrder(cams: GhostCam[], places: GhostCountry[]): GhostCam[] {
  const byCity = new Map<string, GhostCam[]>();
  for (const cam of cams) {
    const list = byCity.get(cam.cityKey) ?? [];
    list.push(cam);
    byCity.set(cam.cityKey, list);
  }
  for (const list of byCity.values()) list.sort((a, b) => (a.kind === b.kind ? 0 : a.kind === "video" ? -1 : 1));
  const seen = new Set<string>();
  const countries: GhostCam[][] = [];
  for (const country of places) {
    const cities: GhostCam[][] = [];
    for (const city of country.cities) {
      const list = byCity.get(city.key);
      if (list) {
        cities.push(list);
        seen.add(city.key);
      }
    }
    countries.push(interleave(cities));
  }
  // Cameras whose city is not in `places` (every camera there failed) go last.
  const rest = [...byCity.entries()].filter(([key]) => !seen.has(key)).flatMap(([, list]) => list);
  return [...interleave(countries), ...rest];
}

/** The cameras a filter keeps. Words match the name, place, city, country and operator. */
export function filterCams(cams: GhostCam[], filter: WallFilter): GhostCam[] {
  const terms = filter.words.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return cams.filter((cam) => {
    if (filter.city && cam.cityKey !== filter.city) return false;
    if (filter.country && cam.countryKey !== filter.country) return false;
    if (!terms.length) return true;
    const hay = `${cam.name} ${cam.place} ${cam.city} ${cam.country} ${cam.countryCode} ${cam.provider}`.toLowerCase();
    return terms.every((term) => hay.includes(term));
  });
}

/** Countries and cities whose name holds `query`, for the place picker. */
export function matchPlaces(places: GhostCountry[], query: string): { countries: GhostCountry[]; cities: Array<GhostCity & { country: GhostCountry }> } {
  const q = query.trim().toLowerCase();
  const countries = q ? places.filter((c) => `${c.name} ${c.code}`.toLowerCase().includes(q)) : places;
  const cities = q
    ? places.flatMap((country) => country.cities.filter((c) => c.name.toLowerCase().includes(q)).map((c) => ({ ...c, country })))
    : [];
  return { countries, cities };
}

/** Every city in picker order (countries largest first, then their cities). */
export function allCities(places: GhostCountry[]): GhostCity[] {
  return places.flatMap((c) => c.cities);
}

/**
 * The city after (or before) `current`. Inside a chosen country it stays in
 * that country; otherwise it walks every city of every country.
 */
export function stepCity(places: GhostCountry[], current: string | null, country: string | null, dir: 1 | -1): GhostCity | null {
  const pool = country ? places.find((c) => c.key === country)?.cities ?? [] : allCities(places);
  if (!pool.length) return null;
  const at = pool.findIndex((c) => c.key === current);
  if (at < 0) return dir > 0 ? pool[0] : pool[pool.length - 1];
  return pool[(at + dir + pool.length) % pool.length];
}

/** A city anywhere but `current`; `rand` returns 0..1 (Math.random in the app). */
export function randomCity(places: GhostCountry[], current: string | null, rand: () => number): GhostCity | null {
  const pool = allCities(places).filter((c) => c.key !== current);
  if (!pool.length) return null;
  return pool[Math.min(pool.length - 1, Math.floor(rand() * pool.length))];
}

export interface GridWindow {
  cols: number;
  tileW: number;
  tileH: number;
  /** One row's height plus the gap. */
  rowH: number;
  rows: number;
  totalH: number;
  /** Index of the first and one past the last tile on screen, with `overscan` rows around. */
  first: number;
  last: number;
}

/**
 * Which tiles of a `count`-tile grid lie on screen: the wall renders only
 * these, so ten thousand cameras cost as much as the forty in view.
 */
export function gridWindow({
  width,
  height,
  scrollTop,
  count,
  minTile,
  gap = TILE_GAP,
  overscan = 1,
}: {
  width: number;
  height: number;
  scrollTop: number;
  count: number;
  minTile: number;
  gap?: number;
  overscan?: number;
}): GridWindow {
  const cols = Math.max(1, Math.floor((Math.max(0, width) + gap) / (minTile + gap)));
  const tileW = Math.max(0, (width - gap * (cols - 1)) / cols);
  const tileH = Math.round(tileW * TILE_RATIO);
  const rowH = tileH + gap;
  const rows = Math.ceil(count / cols);
  const totalH = Math.max(0, rows * rowH - gap);
  if (!rowH || !count) return { cols, tileW, tileH, rowH, rows, totalH, first: 0, last: 0 };
  const firstRow = Math.max(0, Math.floor(scrollTop / rowH) - overscan);
  const lastRow = Math.min(rows, Math.ceil((scrollTop + height) / rowH) + overscan);
  return { cols, tileW, tileH, rowH, rows, totalH, first: firstRow * cols, last: Math.min(count, lastRow * cols) };
}
