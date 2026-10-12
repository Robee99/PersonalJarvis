import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ArmoryMcpCatalogue, mergeMcpCatalogue } from "./ArmoryMcpCatalogue";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const jarvis = [
  { name: "windows-mcp", display: "Windows control", description: "Desktop actions", enabled: true, status: "running", tools: [{ name: "click" }] },
  { name: "fetch", display: "Fetch", enabled: false, status: "stopped", tools: [] },
];
const hermes = [
  { name: "WINDOWS-MCP", transport: "stdio", enabled: true },
  { name: "deepwiki", transport: "stdio", enabled: false },
];

function renderCatalogue() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ArmoryMcpCatalogue />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("unified MCP catalogue", () => {
  it("merges same-name servers across runtimes into one entry", () => {
    const rows = mergeMcpCatalogue(
      { jarvis, jarvisError: null, hermes, hermesError: null },
      undefined,
    );
    expect(rows).toHaveLength(3);
    const windows = rows.find((row) => row.key === "windows-mcp");
    expect(windows?.jarvis?.status).toBe("running");
    expect(windows?.hermes?.enabled).toBe(true);
  });

  it("shows each runtime's state once and keeps its controls separate", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
      String(input) === "/api/mcps"
        ? json({ servers: jarvis })
        : json({ mcp_servers: hermes }),
    );
    renderCatalogue();

    await waitFor(() => expect(screen.getByText(/3 unique configured/)).toBeTruthy());
    expect(screen.getAllByText("Windows control")).toHaveLength(1);
    expect(screen.getByText("JARVIS · running · 1 tool")).toBeTruthy();
    expect(screen.getByText("Hermes · enabled")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Disable JARVIS" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Disable Hermes" })).toBeTruthy();
  });

  it("loads available servers on demand and routes Hermes actions correctly", async () => {
    const calls: { url: string; body?: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      calls.push({ url, body: init?.body as string | undefined });
      if (url === "/api/mcps") return json({ servers: jarvis });
      if (url === "/api/hermes/catalog") {
        return json({
          available: true,
          error: null,
          mcp_servers: [
            { name: "deepwiki", description: "Repository research", auth: "none", installed: false },
            { name: "google-drive", description: "Drive files", auth: "oauth", installed: false },
          ],
          skills: [],
        });
      }
      if (url === "/api/hermes/action") return json({ ok: true });
      return json({ mcp_servers: hermes });
    });
    renderCatalogue();

    await waitFor(() => expect(screen.getByText(/3 unique configured/)).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Load available Hermes MCPs" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Add to Hermes" })).toBeTruthy());
    expect(screen.getAllByText("deepwiki")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Enable Hermes" }));

    await waitFor(() => expect(calls.some((call) => call.url === "/api/hermes/action")).toBe(true));
    const action = calls.find((call) => call.url === "/api/hermes/action");
    expect(JSON.parse(action?.body ?? "{}")).toEqual({ kind: "mcp", op: "enable", name: "deepwiki" });
  });

  it("routes JARVIS enable and disable to the JARVIS MCP API", async () => {
    const calls: { url: string; method?: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      calls.push({ url, method: init?.method });
      if (url === "/api/mcps") return json({ servers: jarvis });
      if (url === "/api/mcps/windows-mcp/disable") return json({ ok: true, enabled: false });
      return json({ mcp_servers: hermes });
    });
    renderCatalogue();

    await waitFor(() => expect(screen.getByRole("button", { name: "Disable JARVIS" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Disable JARVIS" }));
    await waitFor(() => expect(calls.some((call) => call.url === "/api/mcps/windows-mcp/disable")).toBe(true));
    expect(calls.find((call) => call.url === "/api/mcps/windows-mcp/disable")?.method).toBe("POST");
  });
});
