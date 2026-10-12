import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The HUD's memory panel against mocked routes: the project list, the search
 * over items and notes, selecting and opening in place (with a back stack),
 * and acting on a selection — asking the assistant, jumping to the wiki.
 */

// "Ask" goes into the HUD's chat — the front page's agent-chat store.
const send = vi.fn(async (_text: string) => "sent" as const);

import { useI18nStore } from "@/i18n";
import { useEventStore } from "@/store/events";
import { useAgentChatStore } from "@/store/agentChat";
import { HudMemoryPanel } from "@/components/deck/HudMemoryPanel";
import { matchMemoryNodes, previewMarkdown } from "@/components/deck/hudMemoryApi";

const entity = (id: string, kind: string, title: string, status = "blue") => ({
  id,
  kind,
  title,
  status,
  status_reason: `${title} reason`,
  confidence: 1,
  origin: "native",
  last_activity_ms: 1,
  last_updated_ms: 1,
  priority_score: 1,
  priority_reason: "",
  source: { source_kind: "mission", source_id: id },
});

const PROJECTS = {
  projects: [
    {
      ...entity("p-atlas", "PROJECT", "Atlas launch", "red"),
      summary: "Ship the Atlas rocket UI",
      counts: { conversations: 2, open_tasks: 3, open_blockers: 1, decisions: 1, suggested: 0 },
    },
    {
      ...entity("p-forge", "PROJECT", "Forge", "green"),
      summary: "Workshop tooling",
      counts: { conversations: 0, open_tasks: 0, open_blockers: 0, decisions: 0, suggested: 0 },
    },
  ],
  last_run: null,
};

const ATLAS = {
  ...PROJECTS.projects[0],
  tasks: [entity("t-1", "TASK", "Wire telemetry")],
  blockers: [entity("b-1", "BLOCKER", "Thruster vendor late", "red")],
  decisions: [],
  milestones: [],
  next_actions: [{ action: "Resolve blocker: Thruster vendor late", entity_id: "b-1", why: "blocks launch" }],
};

const BLOCKER = {
  ...entity("b-1", "BLOCKER", "Thruster vendor late", "red"),
  evidence: [{ quote: "the vendor slipped two weeks", source: { source_kind: "conversation" } }],
  relations: { outgoing: [], incoming: [] },
  status_history: [],
  projects: ["p-atlas"],
};

const GRAPH = {
  nodes: [
    { ...entity("t-1", "TASK", "Wire telemetry"), color: "#00f", suggested: false },
    { ...entity("b-1", "BLOCKER", "Thruster vendor late", "red"), color: "#f00", suggested: false },
    { ...entity("p-atlas", "PROJECT", "Atlas launch"), color: "#f00", suggested: false },
  ],
  edges: [],
  status_colours: {},
};

const WIKI_HITS = {
  ok: true,
  query: "thruster",
  hits: [{ slug: "thruster-notes", title: "Thruster notes", path: "notes/thruster-notes.md", snippet: "vendor list", score: 2 }],
};

const WIKI_PAGE = {
  ok: true,
  slug: "thruster-notes",
  title: "Thruster notes",
  path: "notes/thruster-notes.md",
  kind: "note",
  body_md: "---\ntitle: x\n---\n# Thruster notes\n\nThe **vendor** list for [[atlas-launch]].",
  wikilinks: ["atlas-launch"],
};

function mockFetch() {
  const calls: string[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
    if (url === "/api/brain-map/projects") return json(PROJECTS);
    if (url === "/api/brain-map/projects/p-atlas") return json(ATLAS);
    if (url === "/api/brain-map/entities/b-1") return json(BLOCKER);
    if (url.startsWith("/api/brain-map/graph")) return json(GRAPH);
    if (url.startsWith("/api/wiki/search")) return json(WIKI_HITS);
    if (url === "/api/wiki/page/thruster-notes") return json(WIKI_PAGE);
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <HudMemoryPanel />
    </QueryClientProvider>,
  );
}

describe("HudMemoryPanel", () => {
  beforeEach(() => {
    useI18nStore.getState().setUi("en", { push: false });
    useEventStore.setState({ assistantName: "Nova", activeSection: "deck", wikiPageRequest: null, toasts: [] });
    send.mockClear();
    useAgentChatStore.setState({ send });
    // These cases read the project list; the orb (the panel's default) has its own tests.
    window.localStorage.setItem("hud3.memory.view", "list");
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("lists projects, opens one in place and follows a next action into its item and back", async () => {
    mockFetch();
    renderPanel();
    const rows = await screen.findAllByTestId("hud-memory-project");
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("Atlas launch"),
      expect.stringContaining("Forge"),
    ]);
    expect(rows[0].textContent).toContain("3 tasks · 1 blockers");

    fireEvent.click(rows[0]);
    expect(await screen.findByText("Ship the Atlas rocket UI")).toBeTruthy();
    expect(screen.getByTestId("hud-memory-title").textContent).toBe("Atlas launch");
    fireEvent.click(await screen.findByTestId("hud-memory-action"));

    // The item, with its evidence, without leaving the HUD.
    await waitFor(() => expect(screen.getByTestId("hud-memory-title").textContent).toBe("Thruster vendor late"));
    expect(screen.getByText(/the vendor slipped two weeks/)).toBeTruthy();

    // Back returns to the project, then to the list.
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    await waitFor(() => expect(screen.getByTestId("hud-memory-title").textContent).toBe("Atlas launch"));
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    expect(await screen.findAllByTestId("hud-memory-project")).toHaveLength(2);
  });

  it("searches items and notes, opens a note and jumps to it in the wiki", async () => {
    const calls = mockFetch();
    renderPanel();
    await screen.findAllByTestId("hud-memory-project");

    fireEvent.change(screen.getByTestId("hud-memory-search"), { target: { value: "thruster" } });
    const item = await screen.findByTestId("hud-memory-item");
    expect(item.textContent).toContain("Thruster vendor late");
    expect(item.textContent).toContain("Blocker");
    const note = await screen.findByTestId("hud-memory-note");
    expect(note.textContent).toContain("Thruster notes");
    // A search matches no project here, so the project list is empty.
    expect(screen.queryAllByTestId("hud-memory-project")).toHaveLength(0);
    expect(calls.some((u) => u === "/api/wiki/search?q=thruster&k=8")).toBe(true);

    fireEvent.click(note);
    const body = await screen.findByTestId("hud-memory-note-body");
    expect(body.textContent).toBe("Thruster notes\n\nThe vendor list for atlas-launch.");

    fireEvent.click(screen.getByTestId("hud-memory-open-wiki"));
    expect(useEventStore.getState().activeSection).toBe("memory");
    expect(useEventStore.getState().wikiPageRequest?.slug).toBe("thruster-notes");
  });

  it("Enter opens the first hit, and Ask sends a chat turn about the selection", async () => {
    mockFetch();
    renderPanel();
    await screen.findAllByTestId("hud-memory-project");
    const input = screen.getByTestId("hud-memory-search");
    fireEvent.change(input, { target: { value: "atlas" } });
    await waitFor(() => expect(screen.getAllByTestId("hud-memory-project")).toHaveLength(1));
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(screen.getByTestId("hud-memory-title").textContent).toBe("Atlas launch"));

    await act(async () => {
      fireEvent.click(screen.getByTestId("hud-memory-ask"));
    });
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0][0]).toContain("Atlas launch");
    expect(useEventStore.getState().toasts.at(-1)?.message).toContain("Nova");
  });

  it("says the memory is unreachable instead of showing an empty list", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("boom", { status: 500 })));
    renderPanel();
    expect(await screen.findByText("Memory is not reachable.")).toBeTruthy();
  });
});

describe("hudMemoryApi helpers", () => {
  it("matches items by every word, title prefix first, projects left out", () => {
    const nodes = [
      { ...entity("a", "TASK", "Fix the thruster mount"), priority_score: 5 },
      { ...entity("b", "TASK", "Thruster vendor"), priority_score: 1 },
      { ...entity("c", "PROJECT", "Thruster programme"), priority_score: 9 },
    ] as Parameters<typeof matchMemoryNodes>[0];
    expect(matchMemoryNodes(nodes, "thruster").map((n) => n.id)).toEqual(["b", "a"]);
    expect(matchMemoryNodes(nodes, "thruster mount").map((n) => n.id)).toEqual(["a"]);
  });

  it("turns a page's markdown into a short plain preview", () => {
    expect(previewMarkdown("# Title\n\n[[a|Alias]] and [link](x) `code`")).toBe("Title\n\nAlias and link code");
    expect(previewMarkdown("x".repeat(20), 10)).toBe(`${"x".repeat(10)}…`);
  });
});
