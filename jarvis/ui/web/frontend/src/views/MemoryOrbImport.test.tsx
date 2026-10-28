import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MemoryOrbImport, importSummary, type ImportState } from "./MemoryOrbImport";

const base: ImportState = {
  job_id: "j1",
  phase: "importing",
  source: "C:\\AI DATA",
  obsidian_vault: true,
  discovered: 4,
  supported: 3,
  imported: 2,
  updated: 0,
  unchanged: 0,
  skipped: 1,
  failed: 0,
  current: "Projects/Falcon.md",
  skip_reasons: { "unsupported type": 1 },
  problems: [],
  error: "",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

describe("Import Data on the memory orb", () => {
  it("summarises each phase in plain words", () => {
    expect(importSummary({ ...base, phase: "done" })).toBe("Done: 2 new · 0 updated · 0 unchanged · 1 skipped.");
    expect(importSummary({ ...base, phase: "cancelled" })).toContain("Import again to finish");
    expect(importSummary({ ...base, phase: "failed", error: "disk full" })).toContain("Failed: disk full");
  });

  it("imports the typed folder, polls the job and refreshes the orb when it ends", async () => {
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/wiki/import") return json({ ...base });
      if (url === "/api/wiki/import/j1")
        return json({
          ...base,
          phase: "done",
          running: false,
          imported: 3,
          conversations: 2,
          skipped_types: { ".png: picture (no text)": 20 },
        });
      return json({}, 404);
    });

    render(
      <QueryClientProvider client={client}>
        <MemoryOrbImport />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /import data/i }));
    fireEvent.click(screen.getByRole("button", { name: /type a path instead/i }));
    fireEvent.change(screen.getByLabelText("Folder or file to import"), { target: { value: "C:\\AI DATA" } });
    fireEvent.click(screen.getByRole("button", { name: "Import" }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Done: 3 new"), {
      timeout: 3000,
    });
    const started = fetchMock.mock.calls.find(([u]) => String(u) === "/api/wiki/import");
    expect(JSON.parse(String(started?.[1]?.body))).toEqual({ path: "C:\\AI DATA" });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["orb", "sources"] });
    expect(screen.getByText("Obsidian vault detected.")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("2 conversation pages");
    expect(screen.getByText("Skipped 20 × .png: picture (no text)")).toBeTruthy();
  });

  it("shows why a source was refused", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json({ detail: "That folder is already part of Jarvis memory." }, 400),
    );
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryOrbImport />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /import data/i }));
    fireEvent.click(screen.getByRole("button", { name: /type a path instead/i }));
    fireEvent.change(screen.getByLabelText("Folder or file to import"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Import" }));
    expect((await screen.findByRole("alert")).textContent).toBe("That folder is already part of Jarvis memory.");
  });

  it("hides the path box until asked and imports a picked export file at once", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/wiki/import/pick-file") return json({ path: "C:\\Users\\r\\Downloads\\export.zip" });
      if (url === "/api/wiki/import") return json({ ...base, running: false, phase: "done" });
      return json({}, 404);
    });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryOrbImport />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /import data/i }));
    expect(screen.queryByLabelText("Folder or file to import")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /choose file/i }));

    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u) === "/api/wiki/import")).toBe(true),
    );
    const started = fetchMock.mock.calls.find(([u]) => String(u) === "/api/wiki/import");
    expect(JSON.parse(String(started?.[1]?.body))).toEqual({ path: "C:\\Users\\r\\Downloads\\export.zip" });
  });

  it("deletes all notes with a second click, no typing", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(json({ pages: 2130, backup: "C:\\wiki-backups\\x.tar.gz" }));
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryOrbImport />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /import data/i }));
    fireEvent.click(screen.getByRole("button", { name: /delete all notes/i }));
    fireEvent.click(screen.getByRole("button", { name: /yes, delete all notes/i }));

    expect((await screen.findByText(/Deleted 2130 notes/)).textContent).toContain("wiki-backups");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/wiki/wipe");
    expect(JSON.parse(String(init?.body))).toEqual({ confirm: "DELETE ALL NOTES" });
  });
});
