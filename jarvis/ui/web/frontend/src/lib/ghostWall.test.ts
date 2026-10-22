import { describe, expect, it } from "vitest";
import type { CctvCamera, CctvCatalog } from "@/lib/cctv";
import type { LiveStream } from "@/lib/live";
import {
  buildCams,
  filterCams,
  gridWindow,
  interleave,
  matchPlaces,
  placesOf,
  randomCity,
  stepCity,
  wallOrder,
} from "@/lib/ghostWall";

/** The Ghost camera wall's rules, on a small catalog of three countries. */

function cam(id: string, city: string, country: string, code: string, extra: Partial<CctvCamera> = {}): CctvCamera {
  return { id, name: `${city} cam ${id}`, city, city_id: city.toLowerCase(), country, country_code: code, provider: `${city} roads`, lat: 10, lon: 20, feed: "image", heading: null, ...extra };
}

const CATALOG: CctvCatalog = {
  available: true,
  reason: null,
  gev: "http://127.0.0.1:4173",
  cameras: [
    cam("a1", "Austin", "United States", "US"),
    cam("a2", "Austin", "United States", "US"),
    cam("a3", "Austin", "United States", "US"),
    cam("h1", "Houston", "United States", "US"),
    cam("v1", "Houston", "United States", "US", { feed: "video" }),
    cam("l1", "London", "United Kingdom", "GB", { provider: "Transport for London" }),
    cam("l2", "London", "United Kingdom", "GB"),
    cam("x1", "Nowhere", "", ""),
  ],
  cities: [],
  fetched_at: 0,
};

const TOKYO: LiveStream = { key: "tokyo-shinjuku", name: "Tokyo", short: "Tokyo", kind: "camera", group: "asia", place: "Shinjuku skyline", embed: "abcdefghijk", page: "https://www.youtube.com/watch?v=abcdefghijk" };
const LONDON: LiveStream = { ...TOKYO, key: "london-notting-hill", name: "London", place: "Notting Hill Gate" };
const NEWS: LiveStream = { ...TOKYO, key: "bbc", name: "BBC", kind: "news" };

describe("the Ghost camera wall's data", () => {
  const cams = buildCams(CATALOG, [TOKYO, LONDON, NEWS]);
  const places = placesOf(cams);

  it("takes GEV's image cameras and the YouTube city cameras, placed in a country", () => {
    expect(cams.map((c) => c.id)).toEqual(["a1", "a2", "a3", "h1", "l1", "l2", "x1", "yt:tokyo-shinjuku", "yt:london-notting-hill"]);
    const tokyo = cams.find((c) => c.id === "yt:tokyo-shinjuku")!;
    expect(tokyo).toMatchObject({ kind: "video", country: "Japan", countryCode: "JP", countryKey: "jp", city: "Tokyo" });
    // The YouTube London camera joins GEV's London.
    expect(cams.find((c) => c.id === "yt:london-notting-hill")!.cityKey).toBe(cams.find((c) => c.id === "l1")!.cityKey);
  });

  it("lists countries and their cities, largest first, the unnamed country last", () => {
    expect(places.map((c) => [c.key, c.count])).toEqual([["us", 4], ["gb", 3], ["jp", 1], ["zz", 1]]);
    expect(places[0].cities.map((c) => [c.name, c.count])).toEqual([["Austin", 3], ["Houston", 1]]);
    expect(places[1].cities[0]).toMatchObject({ name: "London", count: 3, video: 1 });
  });

  it("leaves out a city whose cameras show no picture", () => {
    const without = placesOf(cams, new Set(["h1"]));
    expect(without.find((c) => c.key === "us")!.cities.map((c) => c.name)).toEqual(["Austin"]);
    expect(placesOf(cams, new Set(["x1"])).map((c) => c.key)).not.toContain("zz");
  });

  it("mixes countries and cities, a city's video first", () => {
    const order = wallOrder(cams, places).map((c) => c.id);
    expect(order.slice(0, 4)).toEqual(["a1", "yt:london-notting-hill", "yt:tokyo-shinjuku", "x1"]);
    expect(order).toHaveLength(cams.length);
    expect(interleave([[1, 2, 3], [4], [5, 6]])).toEqual([1, 4, 5, 2, 6, 3]);
  });

  it("filters by country, city and words across name, city, country and operator", () => {
    const usa = filterCams(cams, { country: "us", city: null, words: "" });
    expect(usa.map((c) => c.id)).toEqual(["a1", "a2", "a3", "h1"]);
    const houston = places[0].cities[1].key;
    expect(filterCams(cams, { country: null, city: houston, words: "" }).map((c) => c.id)).toEqual(["h1"]);
    expect(filterCams(cams, { country: null, city: null, words: "transport london" }).map((c) => c.id)).toEqual(["l1"]);
    expect(filterCams(cams, { country: null, city: null, words: "japan" }).map((c) => c.id)).toEqual(["yt:tokyo-shinjuku"]);
  });

  it("finds countries and cities by name for the picker", () => {
    expect(matchPlaces(places, "").countries).toHaveLength(4);
    const found = matchPlaces(places, "lon");
    expect(found.countries).toEqual([]);
    expect(found.cities.map((c) => [c.name, c.country.key])).toEqual([["London", "gb"]]);
    expect(matchPlaces(places, "united").countries.map((c) => c.key)).toEqual(["us", "gb"]);
  });

  it("steps to the next city inside a country or across the world, and picks a random one elsewhere", () => {
    const austin = places[0].cities[0].key;
    expect(stepCity(places, austin, "us", 1)?.name).toBe("Houston");
    expect(stepCity(places, places[0].cities[1].key, "us", 1)?.name).toBe("Austin");
    expect(stepCity(places, places[0].cities[1].key, null, 1)?.name).toBe("London");
    expect(stepCity(places, null, null, -1)?.name).toBe("Nowhere");
    expect(randomCity(places, austin, () => 0)?.name).toBe("Houston");
    expect(randomCity(places, austin, () => 0.999)?.name).toBe("Nowhere");
    expect(randomCity([], null, () => 0)).toBeNull();
  });
});

describe("the wall's window", () => {
  it("draws only the rows on screen, whatever the camera count", () => {
    const w = gridWindow({ width: 1000, height: 600, scrollTop: 0, count: 10_000, minTile: 244, gap: 4 });
    expect(w.cols).toBe(4);
    expect(w.tileW).toBe(247);
    expect(w.rowH).toBe(Math.round(247 * 9 / 16) + 4);
    expect(w.first).toBe(0);
    expect(w.last).toBe(Math.ceil(600 / w.rowH + 1) * 4);
    expect(w.last).toBeLessThan(40);
    const down = gridWindow({ width: 1000, height: 600, scrollTop: w.rowH * 100, count: 10_000, minTile: 244, gap: 4 });
    expect(down.first).toBe(99 * 4);
    expect(down.last - down.first).toBeLessThan(40);
    expect(gridWindow({ width: 1000, height: 600, scrollTop: 0, count: 0, minTile: 244 }).last).toBe(0);
  });
});
