import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { JarvisRail, modelChip, type RailVision } from "./JarvisRail";
import { useEventStore } from "@/store/events";
import { useRailStore } from "@/store/rail";

/**
 * The right-hand rail from the owner's reference (2026-10-25): ring, state
 * and model chips, RING / CUBE / FACE, and EYES / WATCH / HOLO / FOCUS /
 * REFLEX. The vision switches only call what VisionControls hands in; FOCUS
 * talks to the server-side session.
 */
function vision(over: Partial<RailVision> = {}): RailVision & { calls: string[] } {
  const calls: string[] = [];
  return {
    webcam: false,
    screen: false,
    watching: false,
    onEyes: () => calls.push("eyes"),
    onScreen: () => calls.push("screen"),
    onWatch: () => calls.push("watch"),
    onQuiet: () => calls.push("quiet"),
    onStopAll: () => calls.push("stop"),
    detail: null,
    calls,
    ...over,
  };
}

const posts: { url: string; body: unknown }[] = [];
let focusState = { active: false, remaining_s: 0, target: "", deferred: false, drifts: 0, on_target_percent: 100, goal: "" };

function show(v: RailVision) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <JarvisRail vision={v} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  posts.length = 0;
  focusState = { active: false, remaining_s: 0, target: "", deferred: false, drifts: 0, on_target_percent: 100, goal: "" };
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      posts.push({ url, body: JSON.parse(String(init.body ?? "{}")) });
      if (url.endsWith("/start")) focusState = { ...focusState, active: true, remaining_s: 1800, deferred: true };
      return new Response(JSON.stringify({ text: "ok", ...focusState }), { status: 200 });
    }
    return new Response(JSON.stringify(focusState), { status: 200 });
  });
  useEventStore.setState({ voiceState: "listening", brainModel: "claude-opus-5-5", activeSection: "agents" });
  useRailStore.setState({ avatar: "ring", holo: false, reflexOpen: false, folded: false, mapOpen: false });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("JarvisRail", () => {
  it("reads a model id as the chip shows it", () => {
    expect(modelChip("claude-opus-5-5")).toBe("OPUS 5.5");
    expect(modelChip("gemma-4b-qat")).toBe("GEMMA 4B QAT");
    expect(modelChip("unsloth/NVIDIA-Nemotron-3.5-Bolt-E4B")).toBe("NVIDIA NEMOTRON 3.5 BOLT E4B");
  });

  it("shows the state, the model and the five switches, all off", () => {
    show(vision());
    expect(screen.getByText("Listening")).toBeTruthy();
    expect(screen.getByText("OPUS 5.5")).toBeTruthy();
    for (const id of ["rail-eyes", "rail-watch", "rail-holo", "rail-focus-switch", "rail-reflex-switch"]) {
      expect(screen.getByTestId(id).getAttribute("aria-pressed")).toBe("false");
    }
    expect(screen.getByTestId("rail-reflex-switch").textContent).toContain("hidden");
  });

  it("hands the eyes and the watch to the vision organs", () => {
    const v = vision();
    show(v);
    fireEvent.click(screen.getByTestId("rail-eyes"));
    fireEvent.click(screen.getByTestId("rail-watch"));
    expect(v.calls).toEqual(["eyes", "watch"]);
  });

  it("switches the figure in the ring and remembers it", () => {
    show(vision());
    fireEvent.click(screen.getByRole("radio", { name: "Cube" }));
    expect(useRailStore.getState().avatar).toBe("cube");
    expect(screen.getByRole("radio", { name: "Cube" }).getAttribute("aria-checked")).toBe("true");
  });

  it("HOLO opens the brain map from the HUD", () => {
    show(vision());
    fireEvent.click(screen.getByTestId("rail-holo"));
    expect(useRailStore.getState().holo).toBe(true);
    expect(useEventStore.getState().activeSection).toBe("chats");
  });

  it("FOCUS starts a thirty-minute session and shows the deferred lock", async () => {
    show(vision());
    fireEvent.click(screen.getByTestId("rail-focus-switch"));
    await waitFor(() => expect(posts).toEqual([{ url: "/api/focus/start", body: { minutes: 30, goal: "" } }]));
    await waitFor(() => expect(document.body.textContent).toContain("Go to your work"));
    await screen.findByTestId("rail-focus");
    expect(screen.getByTestId("rail-focus-switch").textContent).toContain("30:00");
  });

  it("REFLEX lists the commands answered without a model", () => {
    show(vision());
    fireEvent.click(screen.getByTestId("rail-reflex-switch"));
    expect(screen.getByTestId("rail-reflex").textContent).toContain("volume 40");
  });

  it("keeps only the switches on the HUD, where the big orb already shows the state", () => {
    useEventStore.setState({ activeSection: "chats" });
    show(vision());
    expect(screen.queryByText("Listening")).toBeNull();
    expect(screen.getByTestId("rail-eyes")).toBeTruthy();
  });

  it("shows its ring over the brain map, as in the reference", () => {
    useEventStore.setState({ activeSection: "chats" });
    useRailStore.setState({ mapOpen: true, folded: true });
    show(vision());
    expect(screen.getByText("Listening")).toBeTruthy();
    // Folding applies off the Stark screens only.
    expect(screen.getByTestId("rail-eyes")).toBeTruthy();
  });

  it("folds to its ring on a working page", () => {
    useRailStore.setState({ folded: true });
    show(vision());
    expect(screen.queryByTestId("rail-eyes")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Unfold the rail" }));
    expect(screen.getByTestId("rail-eyes")).toBeTruthy();
  });
});
