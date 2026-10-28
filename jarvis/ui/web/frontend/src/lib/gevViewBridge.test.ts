import { afterEach, describe, expect, it, vi } from "vitest";

import { clearPendingGeVView, publishGeVViewResult, readPendingGeVView, requestGeVView } from "./gevViewBridge";
import { useIntelStore } from "@/store/intel";

afterEach(() => {
  clearPendingGeVView();
  vi.restoreAllMocks();
});

describe("God’s Eye View result bridge", () => {
  it("publishes a view from the approved GEV tool and persists it for a later mount", () => {
    const dispatch = vi.spyOn(window, "dispatchEvent");
    const view = {
      camera: { lat: 30.2672, lon: -97.7431, altitude_m: 5000 },
      layers: ["flights"],
    };

    publishGeVViewResult(
      "mcp__gev__show_in_gods_eye_view",
      JSON.stringify({ view }),
      "call-42",
    );

    expect(dispatch).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "jarvis:gev-view",
        detail: expect.objectContaining({ view, callId: "call-42" }),
      }),
    );
    expect(readPendingGeVView()).toEqual(view);
  });

  it("ignores other tools, missing output, and malformed tool responses", () => {
    const dispatch = vi.spyOn(window, "dispatchEvent");
    const output = JSON.stringify({ view: { camera: { lat: 1, lon: 2, altitude_m: 3 } } });

    publishGeVViewResult("filesystem__read_file", output, "call-1");
    publishGeVViewResult("mcp__gev__show_in_gods_eye_view", null, "call-2");
    publishGeVViewResult("mcp__gev__show_in_gods_eye_view", "not json", "call-3");

    expect(dispatch).not.toHaveBeenCalled();
    expect(readPendingGeVView()).toBeNull();
  });

  it("flies to a view the backend sent with WorldIntelCommand", () => {
    // Live 2026-10-26: voice "take me to Mumbai in God's Eye View" never moved the globe.
    const dispatch = vi.spyOn(window, "dispatchEvent");
    const view = { camera: { lat: 19.055, lon: 72.8692, altitude_m: 44521 }, layers: [] };

    useIntelStore.getState().applyCommand({ action: "view", view: JSON.stringify(view) });

    expect(dispatch).toHaveBeenCalledWith(
      expect.objectContaining({ type: "jarvis:gev-view", detail: expect.objectContaining({ view }) }),
    );
    expect(readPendingGeVView()).toEqual(view);
  });

  it("ignores a backend view without a camera", () => {
    expect(requestGeVView(JSON.stringify({ layers: [] }), "x")).toBe(false);
    expect(requestGeVView("not json", "x")).toBe(false);
  });
});
