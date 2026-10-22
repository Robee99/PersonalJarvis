import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FIRED_SHOW_MS, GestureIndicator, HandGuide, HandOverlay, HandToggle, guideRowsFor } from "./HandControls";
import { GESTURES } from "./gestures";
import { firedGesture, type HandTracking } from "./useHandTracking";

/** A live tracking state as the hook hands it to the view; no camera. */
function liveTracking(over: Partial<HandTracking> = {}): HandTracking {
  return {
    status: "live",
    detail: "",
    cursors: [],
    gesture: null,
    fired: null,
    stream: null,
    start: () => undefined,
    stop: () => undefined,
    ...over,
  };
}

const activeRows = () =>
  [...document.querySelectorAll("[data-testid='orb-hands-guide'] li[data-active]")].map((li) => li.getAttribute("data-gesture"));

afterEach(() => {
  vi.useRealTimers();
});

describe("the gesture guide", () => {
  it("lists every gesture while hands are live", () => {
    render(<HandToggle tracking={liveTracking()} />);
    const rows = [...document.querySelectorAll("[data-testid='orb-hands-guide'] li")].map((li) => li.getAttribute("data-gesture"));
    expect(rows).toEqual([...GESTURES]);
    expect(activeRows()).toEqual([]);
  });

  it("lights up the gesture the camera recognises", () => {
    const { rerender } = render(<HandToggle tracking={liveTracking({ gesture: { id: "zoom" } })} />);
    expect(activeRows()).toEqual(["zoom"]);
    // A pinch not yet released or held could become either.
    rerender(<HandToggle tracking={liveTracking({ gesture: { id: "pinch", progress: 0.4 } })} />);
    expect(activeRows()).toEqual(["select", "open"]);
    expect(guideRowsFor({ id: "reset", progress: 1 })).toEqual(["reset"]);
  });

  it("marks the gestures the map shown cannot act on", () => {
    render(<HandGuide gesture={null} available={GESTURES.filter((g) => g !== "pan")} />);
    const pan = document.querySelector("li[data-gesture='pan']");
    expect(pan?.textContent).toContain("Not on this map");
    expect(document.querySelector("li[data-gesture='zoom']")?.textContent).not.toContain("Not on this map");
  });

  it("is not shown before tracking is live", () => {
    render(<HandToggle tracking={liveTracking({ status: "loading" })} />);
    expect(screen.queryByTestId("orb-hands-guide")).toBeNull();
  });
});

describe("the live indicator", () => {
  it("names the recognised gesture and shows a held one charging", () => {
    render(<GestureIndicator tracking={{ gesture: { id: "reset", progress: 0.5 }, fired: null }} />);
    const indicator = screen.getByTestId("orb-hands-indicator");
    expect(indicator.getAttribute("data-gesture")).toBe("reset");
    expect(indicator.textContent).toContain("Hold to reset the view");
    expect((indicator.querySelector("[style]") as HTMLElement).style.width).toBe("50%");
  });

  it("says so when no hand is seen", () => {
    render(<GestureIndicator tracking={{ gesture: null, fired: null }} />);
    expect(screen.getByTestId("orb-hands-indicator").getAttribute("data-gesture")).toBe("none");
    expect(screen.getByTestId("orb-hands-indicator").textContent).toContain("Show a hand to the camera");
  });

  it("briefly names what the last gesture did", () => {
    vi.useFakeTimers();
    const { rerender } = render(<GestureIndicator tracking={{ gesture: null, fired: { id: "swipe", step: -1, seq: 1 } }} />);
    expect(screen.getByTestId("orb-hands-fired").textContent).toBe("Previous section");
    act(() => {
      vi.advanceTimersByTime(FIRED_SHOW_MS + 10);
    });
    expect(screen.queryByTestId("orb-hands-fired")).toBeNull();
    rerender(<GestureIndicator tracking={{ gesture: null, fired: { id: "reset", seq: 2 } }} />);
    expect(screen.getByTestId("orb-hands-fired").textContent).toBe("View reset");
  });
});

describe("the overlay", () => {
  it("draws a cursor per hand with its pose and a hold ring while charging", () => {
    render(
      <HandOverlay
        tracking={liveTracking({
          cursors: [
            { slot: 0, pose: "pinch", x: 0.25, y: 0.5, progress: 0.3 },
            { slot: 1, pose: "thumbs", x: 0.75, y: 0.5 },
          ],
          gesture: { id: "pinch", progress: 0.3 },
        })}
      />,
    );
    const poses = [...document.querySelectorAll("[data-pose]")].map((el) => el.getAttribute("data-pose"));
    expect(poses).toEqual(["pinch", "thumbs"]);
    expect(document.querySelector("[data-pose='pinch'] svg")).not.toBeNull();
    expect(document.querySelector("[data-pose='thumbs'] svg")).toBeNull();
    expect(screen.getByTestId("orb-hands-indicator").getAttribute("data-gesture")).toBe("pinch");
  });

  it("is absent unless tracking is live", () => {
    render(<HandOverlay tracking={liveTracking({ status: "camera" })} />);
    expect(screen.queryByTestId("orb-hands-overlay")).toBeNull();
  });
});

describe("which actions the indicator names", () => {
  it("names the one-off gestures and not the continuous ones", () => {
    expect(firedGesture({ type: "select", point: { x: 0, y: 0 } })).toEqual({ id: "select" });
    expect(firedGesture({ type: "open", point: { x: 0, y: 0 } })).toEqual({ id: "open" });
    expect(firedGesture({ type: "confirm" })).toEqual({ id: "confirm" });
    expect(firedGesture({ type: "section", step: 1 })).toEqual({ id: "swipe", step: 1 });
    expect(firedGesture({ type: "reset" })).toEqual({ id: "reset" });
    for (const action of [
      { type: "hover", point: { x: 0, y: 0 } },
      { type: "drag", dx: 0, dy: 0 },
      { type: "zoom", scale: 2 },
      { type: "release" },
    ] as const)
      expect(firedGesture(action)).toBeNull();
  });
});
