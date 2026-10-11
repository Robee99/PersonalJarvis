/**
 * The Model page's one list: Hermes decides, models on this computer, free cloud.
 * Pins that a local llama.cpp server is shown with its state and that one click
 * makes it Hermes's model.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { BrainPicker } from "./BrainPicker";

const LIST = {
  hermes_brain: true,
  selection: "hermes-agent",
  servers: [
    {
      url: "http://127.0.0.1:11437", port: 11437, kind: "llama.cpp", state: "running",
      models: ["qwen"], hermes_provider: "local-qwen", hermes_model: "qwen",
    },
    {
      url: "http://127.0.0.1:11438", port: 11438, kind: "llama.cpp", state: "off",
      models: [], hermes_provider: "local-gemma", hermes_model: "gemma-4-12b-qat",
    },
  ],
};

const MODELS = {
  provider: "hermes", current_model: "hermes-agent", source: "curated", fetched_at: 0,
  models: [
    { id: "hermes-agent", label: "Hermes decides" },
    { id: "nous::stepfun/step-3.7-flash:free", label: "Step 3.7 Flash (Nous free)" },
    { id: "local-qwen::qwen", label: "Qwen (local Hermes provider)" },
  ],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("BrainPicker", () => {
  it("lists Hermes decides, the local servers with their state, and the free cloud models", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => (
      url.startsWith("/api/local-servers") ? json(LIST) : json(MODELS)
    )));
    render(<BrainPicker />);

    const qwen = await screen.findByRole("radio", { name: /qwen.*11437/i });
    expect(qwen.textContent).toContain("Running");
    expect(screen.getByRole("radio", { name: /gemma-4-12b-qat/ }).textContent).toContain("Not running");
    expect(screen.getByRole("radio", { name: /Hermes decides/ }).getAttribute("aria-checked")).toBe("true");
    expect(await screen.findByRole("radio", { name: /Step 3\.7 Flash/ })).toBeTruthy();
    // The local Hermes providers come from the scan, not twice from the catalogue.
    expect(screen.queryByText("Qwen (local Hermes provider)")).toBeNull();
  });

  it("one click on a local server makes it Hermes's model", async () => {
    let selection = "hermes-agent";
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/local-servers/use") {
        expect(JSON.parse(String(init?.body))).toEqual({ url: "http://127.0.0.1:11437", model: "qwen" });
        selection = "local-qwen::qwen";
        return json({ ok: true, brain: "hermes", selection });
      }
      if (url.startsWith("/api/local-servers")) return json({ ...LIST, selection });
      return json(MODELS);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<BrainPicker />);

    fireEvent.click(await screen.findByRole("radio", { name: /qwen.*11437/i }));

    await waitFor(() =>
      expect(screen.getByRole("radio", { name: /qwen.*11437/i }).getAttribute("aria-checked")).toBe("true"),
    );
    expect(fetchMock).toHaveBeenCalledWith("/api/local-servers/use", expect.objectContaining({ method: "POST" }));
  });
});
