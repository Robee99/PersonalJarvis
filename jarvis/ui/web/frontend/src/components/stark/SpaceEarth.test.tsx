import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useI18nStore } from "@/i18n";
import {
  canDrawWebgl,
  isDaylight,
  latLonToVec,
  ORBITAL_VIEW_KEY,
  readOrbitalLook,
  subsolarPoint,
  sunDirection,
  vecToLatLon,
  writeOrbitalLook,
} from "@/lib/spaceMath";
import { OrbitalView } from "@/views/OrbitalView";

const close = (a: number, b: number, eps: number) => Math.abs(a - b) <= eps;

describe("space math", () => {
  it("puts the Sun over the equator near Greenwich at the March equinox noon", () => {
    const p = subsolarPoint(Date.UTC(2025, 2, 20, 12, 0, 0));
    expect(close(p.lat, 0, 0.6)).toBe(true);
    expect(close(p.lon, 0, 3)).toBe(true);
  });

  it("puts the June solstice Sun over the Tropic of Cancer", () => {
    const p = subsolarPoint(Date.UTC(2025, 5, 21, 0, 0, 0));
    expect(close(p.lat, 23.44, 0.2)).toBe(true);
    // Midnight UTC: the Sun is over the Pacific, near the antimeridian.
    expect(Math.abs(p.lon)).toBeGreaterThan(170);
  });

  it("maps latitude, longitude and height into the Earth-fixed frame and back", () => {
    const [x, y, z] = latLonToVec(0, 0, 0);
    expect([close(x, 1, 1e-9), close(y, 0, 1e-9), close(z, 0, 1e-9)]).toEqual([true, true, true]);
    const north = latLonToVec(90, 0, 0);
    expect(close(north[1], 1, 1e-9)).toBe(true);
    const iss = latLonToVec(30, 60, 420);
    expect(close(Math.hypot(...iss), 1 + 420 / 6371, 1e-9)).toBe(true);
    const back = vecToLatLon(iss);
    expect(close(back.lat, 30, 1e-6) && close(back.lon, 60, 1e-6)).toBe(true);
    // East is towards -Z, the way three.js lays an equirectangular map on a sphere.
    expect(latLonToVec(0, 90, 0)[2]).toBeCloseTo(-1, 9);
  });

  it("knows day from night", () => {
    const at = Date.UTC(2025, 2, 20, 12, 0, 0);
    expect(isDaylight(0, 0, at)).toBe(true);
    expect(isDaylight(0, 180, at)).toBe(false);
    expect(Math.hypot(...sunDirection(at))).toBeCloseTo(1, 9);
  });
});

describe("the Orbital screen's look", () => {
  beforeEach(() => {
    useI18nStore.getState().setUi("en", { push: false });
    window.localStorage.removeItem(ORBITAL_VIEW_KEY);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 404 })),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("is realistic unless HUD was chosen, and keeps the choice", () => {
    expect(readOrbitalLook()).toBe("realistic");
    writeOrbitalLook("hud");
    expect(window.localStorage.getItem(ORBITAL_VIEW_KEY)).toBe("hud");
    expect(readOrbitalLook()).toBe("hud");
  });

  it("reports no WebGL when the page cannot make a context", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    expect(canDrawWebgl()).toBe(false);
  });

  it("falls back to the HUD globe when WebGL is missing, and says so", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <OrbitalView />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("stark-look-hud").getAttribute("aria-pressed")).toBe("true");
    expect((screen.getByTestId("stark-look-realistic") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId("stark-space-unavailable")).toBeTruthy();
    expect(screen.queryByTestId("stark-space-earth")).toBeNull();
    // Choosing HUD is still remembered.
    fireEvent.click(screen.getByTestId("stark-look-hud"));
    expect(window.localStorage.getItem(ORBITAL_VIEW_KEY)).toBe("hud");
  });
});
