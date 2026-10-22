import { describe, expect, test } from "vitest";
import { elevation, gmst, groundTrack, nextPasses, propagate } from "@/lib/orbit";
import type { Station } from "@/lib/publicData";

const EPOCH = Date.UTC(2026, 9, 18, 12, 0, 0);
const ISS: Station = {
  name: "ISS (ZARYA)",
  norad_id: 25544,
  epoch_ms: EPOCH,
  mean_motion: 15.5,
  eccentricity: 0.0004,
  inclination: 51.64,
  raan: 120,
  arg_perigee: 90,
  mean_anomaly: 0,
  period_min: 92.9,
  apogee_km: 422,
  perigee_km: 417,
};

describe("orbit", () => {
  test("an ISS-like element sits about 420 km up and never past its inclination", () => {
    for (let m = 0; m < 200; m += 7) {
      const p = propagate(ISS, EPOCH + m * 60_000);
      expect(p.alt_km).toBeGreaterThan(405);
      expect(p.alt_km).toBeLessThan(430);
      expect(Math.abs(p.lat)).toBeLessThanOrEqual(51.7);
      expect(p.lon).toBeGreaterThanOrEqual(-180);
      expect(p.lon).toBeLessThanOrEqual(180);
    }
    // At perigee with the argument of perigee at 90°, the station is at its most northern point.
    expect(propagate(ISS, EPOCH).lat).toBeCloseTo(51.64, 0);
  });

  test("one period later it is back at the same latitude, about 23° further west", () => {
    const a = propagate(ISS, EPOCH);
    const b = propagate(ISS, EPOCH + (86_400_000 / 15.5));
    expect(b.lat).toBeCloseTo(a.lat, 0);
    const drift = ((a.lon - b.lon + 540) % 360) - 180;
    expect(drift).toBeGreaterThan(20);
    expect(drift).toBeLessThan(26);
  });

  test("the ground track splits at the date line", () => {
    const runs = groundTrack(ISS, EPOCH, 190);
    expect(runs.length).toBeGreaterThan(1);
    for (const run of runs) {
      for (let i = 1; i < run.length; i += 1) expect(Math.abs(run[i].lon - run[i - 1].lon)).toBeLessThan(180);
    }
  });

  test("sidereal time at J2000 noon is 280.46°", () => {
    expect(gmst(Date.UTC(2000, 0, 1, 12)) / (Math.PI / 180)).toBeCloseTo(280.46, 1);
  });

  test("a satellite straight overhead is at 90°, one on the far side below the horizon", () => {
    expect(elevation({ lat: 10, lon: 20 }, { lat: 10, lon: 20, alt_km: 420 })).toBeCloseTo(90, 3);
    expect(elevation({ lat: 10, lon: 20 }, { lat: -10, lon: -160, alt_km: 420 })).toBeLessThan(0);
  });

  test("passes over a mid-latitude city come a few times a day, each a few minutes long", () => {
    const passes = nextPasses(ISS, { lat: 40, lon: -75 }, EPOCH, 24);
    expect(passes.length).toBeGreaterThanOrEqual(2);
    expect(passes.length).toBeLessThanOrEqual(8);
    for (const p of passes) {
      expect(p.end_ms).toBeGreaterThanOrEqual(p.start_ms);
      expect(p.end_ms - p.start_ms).toBeLessThan(12 * 60_000);
      expect(p.max_elev).toBeGreaterThanOrEqual(10);
      expect(p.max_ms).toBeGreaterThanOrEqual(p.start_ms);
    }
  });

  test("a polar observer never sees a 52° orbit high in the sky", () => {
    expect(nextPasses(ISS, { lat: 89, lon: 0 }, EPOCH, 24)).toEqual([]);
  });
});
