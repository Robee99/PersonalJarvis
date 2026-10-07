import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HermesArmoryGroup, skillBreakdown, type HermesInventory } from "./ArmoryHermes";

const inventory: HermesInventory = {
  available: true,
  reason: null,
  home: "C:\\Users\\me\\AppData\\Local\\hermes",
  config_error: null,
  skills: {
    bundled: 41,
    hub: 3,
    local: 5,
    external: 2,
    plugin: 1,
    total: 52,
    truncated: false,
    items: [
      { name: "notes", provenance: "bundled", enabled: true },
      { name: "ascii-art", provenance: "hub", enabled: false },
    ],
  },
  mcp_servers: [
    { name: "github", transport: "stdio", enabled: true },
    { name: "notion", transport: "http", enabled: false },
  ],
  mcp_status_checked: false,
  plugins: {
    available: true,
    enabled: 2,
    total: 6,
    error: null,
    items: [{ name: "kanban", status: "not enabled", source: "bundled", description: "Boards" }],
  },
  toolsets: { available: false, enabled: 0, total: 0, error: "Hermes API server is not reachable" },
  catalog: { available: true, mcp_servers: 65, optional_skills: 152, bundled_skills: 58, error: null },
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function renderGroup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <HermesArmoryGroup />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("Hermes Agent group in the tool armory", () => {
  it("shows the discovered inventory and each part's own error", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(inventory));
    renderGroup();

    await waitFor(() => expect(screen.getByText("52")).toBeTruthy());
    expect(fetchMock).toHaveBeenCalledWith("/api/hermes/inventory");
    expect(screen.getByText("41 bundled · 3 hub · 5 local · 2 external · 1 plugin")).toBeTruthy();
    expect(screen.getByText("1 / 2")).toBeTruthy();
    expect(screen.getByText(/connection not checked/)).toBeTruthy();
    expect(screen.getByText("2 / 6")).toBeTruthy();
    expect(screen.getByText("Hermes API server is not reachable")).toBeTruthy();
    expect(screen.getByText("65")).toBeTruthy();
    expect(screen.getByText("210")).toBeTruthy();
    expect(screen.getByText("58 built in · 152 optional")).toBeTruthy();
  });

  it("says plainly when Hermes is not on this computer", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      json({ ...inventory, available: false, reason: "Hermes not found on this computer" }),
    );
    renderGroup();
    await waitFor(() => expect(screen.getByText("Hermes not found on this computer")).toBeTruthy());
    expect(screen.queryByText("52")).toBeNull();
  });

  it("reports a failed inventory request instead of showing zeros", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ detail: "boom" }, 500));
    renderGroup();
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("Could not read the Hermes Agent inventory"),
    );
  });

  it("breaks skills down by provenance", () => {
    expect(skillBreakdown({ ...inventory.skills, hub: 0 })).toBe(
      "41 bundled · 0 hub · 5 local · 2 external · 1 plugin",
    );
  });

  it("opens a card's list and runs the row's Hermes action", async () => {
    const calls: { url: string; body?: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      calls.push({ url, body: init?.body as string | undefined });
      if (url === "/api/hermes/action") {
        return json({ ok: true, kind: "plugin", op: "enable", name: "kanban", message: "✓ Enabled", restart_needed: true });
      }
      return json(inventory);
    });
    renderGroup();
    await waitFor(() => expect(screen.getByText("2 / 6")).toBeTruthy());

    fireEvent.click(screen.getByText("2 / 6"));
    expect(screen.getByRole("region", { name: "Hermes plugins" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Enable kanban" }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("kanban enabled. Restart Hermes"));
    const action = calls.find((call) => call.url === "/api/hermes/action");
    expect(JSON.parse(action?.body ?? "{}")).toEqual({ kind: "plugin", op: "enable", name: "kanban" });
    expect(calls.some((call) => call.url === "/api/hermes/inventory?refresh=true")).toBe(true);
  });

  it("lists skills with their state and filters them", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(inventory));
    renderGroup();
    await waitFor(() => expect(screen.getByText("52")).toBeTruthy());
    fireEvent.click(screen.getByText("52"));
    expect(screen.getByRole("button", { name: "Disable notes" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Enable ascii-art" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Filter Hermes skills"), { target: { value: "asc" } });
    expect(screen.queryByRole("button", { name: "Disable notes" })).toBeNull();
  });

  it("loads the catalog and offers install only for what is missing", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
      String(input) === "/api/hermes/catalog"
        ? json({
            available: true,
            error: null,
            mcp_servers: [
              { name: "deepwiki", description: "Docs", auth: "none", installed: false },
              { name: "jarvis", description: "", auth: "none", installed: true },
            ],
            skills: [],
          })
        : json(inventory),
    );
    renderGroup();
    await waitFor(() => expect(screen.getByText("65")).toBeTruthy());
    fireEvent.click(screen.getByText("65"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Install deepwiki" })).toBeTruthy());
    expect((screen.getByRole("button", { name: "Installed jarvis" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
