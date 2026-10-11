import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The HUD memory orb without WebGL (jsdom has none): every state says what
 * it is in words — loading, not reachable, nothing yet, no 3D here — and the
 * parts that are not drawing keep working: the legend, the steppers, the
 * focus that follows the conversation, and opening a node in the memory
 * panel around it.
 */

import { useI18nStore } from "@/i18n";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { MemoryOrb3D } from "@/components/deck/MemoryOrb3D";
import { HudMemoryPanel, selectionFromOrb } from "@/components/deck/HudMemoryPanel";
import type { TranscriptLine } from "@/lib/homeTranscript";
import type { OrbRef } from "@/lib/memoryOrbLayout";

const TREE = {
  ok: true,
  communities: [
    {
      id: "p-atlas",
      title: "Atlas launch",
      level: 1,
      parent: null,
      kind: "project",
      colour: "red",
      reason: "blocked",
      progress: 0.3,
      ref: { type: "project", id: "p-atlas" },
      size: 2,
    },
    {
      id: "area-concepts",
      title: "Concepts",
      level: 1,
      parent: null,
      kind: "area",
      colour: "reference",
      reason: "",
      progress: null,
      ref: null,
      size: 1,
    },
  ],
  leaves: [
    { id: "t-1", title: "Wire telemetry", kind: "TASK", colour: "yellow", community: "p-atlas", ref: { type: "entity", id: "t-1" } },
    { id: "b-1", title: "Thruster vendor late", kind: "BLOCKER", colour: "red", community: "p-atlas", ref: { type: "entity", id: "b-1" } },
    { id: "n-1", title: "Hohmann transfer", kind: "note", colour: "reference", community: "area-concepts", ref: { type: "note", slug: "hohmann" } },
  ],
  counts: { red: 1, yellow: 1, blue: 0, green: 0, reference: 1 },
  totals: { notes: 1, projects: 1 },
  legend: {},
  unavailable: [],
};

const EMPTY = { ...TREE, communities: [], leaves: [], counts: { red: 0, yellow: 0, blue: 0, green: 0, reference: 0 } };

function stubFetch(handler: (url: string) => Promise<Response>) {
  const fn = vi.fn((input: RequestInfo | URL) => handler(String(input)));
  vi.stubGlobal("fetch", fn);
  return fn;
}

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));

function renderOrb(onOpen?: (ref: OrbRef) => void) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryOrb3D onOpen={onOpen} />
    </QueryClientProvider>,
  );
}

const line = (id: string, who: "user" | "assistant", text: string): TranscriptLine => ({ id, ts: Date.now(), who, text });

describe("MemoryOrb3D", () => {
  beforeEach(() => {
    useI18nStore.getState().setUi("en", { push: false });
    useHomeStore.setState({ transcript: [] });
    try {
      window.localStorage.clear();
    } catch {
      /* storage unavailable: the panel falls back to its default */
    }
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("says it is loading while the tree is on its way", () => {
    stubFetch(() => new Promise<Response>(() => {}));
    renderOrb();
    expect(screen.getByTestId("memory-orb").dataset.state).toBe("loading");
    expect(screen.getByTestId("memory-orb-state").textContent).toBe("Mapping memory…");
  });

  it("says not reachable when the route fails", async () => {
    stubFetch(() => json({ detail: "boom" }, 500));
    renderOrb();
    await waitFor(() => expect(screen.getByTestId("memory-orb").dataset.state).toBe("error"));
    expect(screen.getByTestId("memory-orb-state").textContent).toContain("not reachable");
  });

  it("says nothing is remembered yet for an empty tree", async () => {
    stubFetch(() => json(EMPTY));
    renderOrb();
    await waitFor(() => expect(screen.getByTestId("memory-orb").dataset.state).toBe("empty"));
    expect(screen.getByTestId("memory-orb-state").textContent).toContain("Nothing remembered yet");
  });

  it("does not call an empty tree empty when its stores could not be read", async () => {
    stubFetch(() => json({ ...EMPTY, unavailable: ["notes", "work"] }));
    renderOrb();
    await waitFor(() => expect(screen.getByTestId("memory-orb").dataset.state).toBe("error"));
  });

  it("falls back to words without WebGL and still steps, shows and opens", async () => {
    const calls = stubFetch(() => json(TREE));
    const onOpen = vi.fn();
    renderOrb(onOpen);
    await waitFor(() => expect(screen.getByTestId("memory-orb").dataset.state).toBe("ready"));
    expect(calls).toHaveBeenCalledWith("/api/wiki/orb-tree", expect.anything());
    const orb = screen.getByTestId("memory-orb");
    expect(orb.dataset.renderer).toBe("none");
    expect(screen.getByTestId("memory-orb-fallback").textContent).toContain("3D is not available");
    expect(orb.textContent).toContain("Blocked");

    // Next from nothing enters the first community, then walks its notes.
    fireEvent.click(screen.getByTestId("memory-orb-next"));
    expect(orb.dataset.focus).toBe("p-atlas");
    expect(screen.getByTestId("memory-orb-title").textContent).toBe("Atlas launch");
    fireEvent.click(screen.getByTestId("memory-orb-next"));
    expect(orb.dataset.focus).toBe("t-1");
    fireEvent.click(screen.getByTestId("memory-orb-next"));
    fireEvent.click(screen.getByTestId("memory-orb-next"));
    expect(orb.dataset.focus).toBe("t-1"); // wrapped round the community
    fireEvent.click(screen.getByTestId("memory-orb-prev"));
    expect(orb.dataset.focus).toBe("b-1");

    fireEvent.click(screen.getByTestId("memory-orb-open"));
    expect(onOpen).toHaveBeenCalledWith({ type: "entity", id: "b-1" });

    // The keyboard does the same; Escape lets go.
    fireEvent.keyDown(orb, { key: "ArrowDown" });
    expect(orb.dataset.focus).toBe("area-concepts");
    fireEvent.keyDown(orb, { key: "Escape" });
    expect(orb.dataset.focus).toBe("");
  });

  it("turns to a node a new transcript line names, but not to one said before it opened", async () => {
    useHomeStore.setState({ transcript: [line("u-0", "user", "how is atlas launch going")] });
    stubFetch(() => json(TREE));
    renderOrb();
    await waitFor(() => expect(screen.getByTestId("memory-orb").dataset.state).toBe("ready"));
    const orb = screen.getByTestId("memory-orb");
    expect(orb.dataset.focus).toBe("");

    act(() => {
      useHomeStore.setState((s) => ({
        transcript: [...s.transcript, line("a-1", "assistant", "The thruster vendor late blocker is still open.")],
      }));
    });
    expect(orb.dataset.focus).toBe("b-1");
    expect(orb.textContent).toContain("named in the conversation");

    act(() => {
      useHomeStore.setState((s) => ({ transcript: [...s.transcript, line("u-2", "user", "open the notes")] }));
    });
    expect(orb.dataset.focus).toBe("b-1"); // generic words move nothing

    act(() => {
      useHomeStore.setState((s) => ({ transcript: [...s.transcript, line("u-3", "user", "explain the Hohmann transfer")] }));
    });
    expect(orb.dataset.focus).toBe("n-1");
  });
});

describe("HudMemoryPanel with the orb", () => {
  beforeEach(() => {
    useI18nStore.getState().setUi("en", { push: false });
    useEventStore.setState({ assistantName: "Nova", activeSection: "deck", wikiPageRequest: null, toasts: [] });
    useHomeStore.setState({ transcript: [] });
    try {
      window.localStorage.clear();
    } catch {
      /* see above */
    }
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("maps an orb node to the panel's own selection", () => {
    expect(selectionFromOrb({ type: "project", id: "p" })).toEqual({ type: "project", id: "p" });
    expect(selectionFromOrb({ type: "entity", id: "e" })).toEqual({ type: "entity", id: "e" });
    expect(selectionFromOrb({ type: "note", slug: "s" })).toEqual({ type: "note", slug: "s" });
    expect(selectionFromOrb({ type: "note" })).toBeNull();
  });

  it("starts on the list without WebGL, switches to the orb and back to it from a detail", async () => {
    stubFetch((url) => {
      if (url === "/api/wiki/orb-tree") return json(TREE);
      if (url === "/api/brain-map/projects") return json({ projects: [] });
      if (url === "/api/brain-map/projects/p-atlas")
        return json({
          id: "p-atlas",
          kind: "PROJECT",
          title: "Atlas launch",
          status: "red",
          status_reason: "",
          summary: "",
          tasks: [],
          blockers: [],
          decisions: [],
          milestones: [],
          next_actions: [],
        });
      return json({}, 404);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <HudMemoryPanel />
      </QueryClientProvider>,
    );
    expect(screen.queryByTestId("memory-orb")).toBeNull();

    fireEvent.click(screen.getByLabelText("Show as 3D orb"));
    await waitFor(() => expect(screen.getByTestId("memory-orb").dataset.state).toBe("ready"));
    expect(window.localStorage.getItem("hud2.memory.view")).toBe("orb");

    fireEvent.click(screen.getByTestId("memory-orb-next"));
    fireEvent.click(screen.getByTestId("memory-orb-open"));
    expect((await screen.findByTestId("hud-memory-title")).textContent).toBe("Atlas launch");
    expect(screen.getByTestId("memory-orb").className).toContain("hidden");

    fireEvent.click(screen.getByText("Back"));
    expect(screen.getByTestId("memory-orb").className).not.toContain("hidden");
    expect(screen.getByTestId("memory-orb").dataset.focus).toBe("p-atlas");
  });
});
