import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Identity translator so rendered text equals the i18n key.
vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
  fill: (template: string) => template,
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: { pushToast: () => void }) => unknown) =>
    selector({ pushToast: vi.fn() }),
}));

import { JarvisAgentSection } from "./JarvisAgentSection";

function row(jarvis: string, label: string, opts: { ready?: boolean; active?: boolean } = {}) {
  return {
    jarvis,
    worker_slug: jarvis,
    env_var: "X",
    env_fallback: null,
    key_set: opts.ready ?? true,
    api_key_set: false,
    dedicated_key_set: false,
    shared_key_set: false,
    oauth_connected: false,
    credential_source: "none",
    secret_key: null,
    dashboard_url: null,
    credential_help: null,
    is_active_brain: opts.active ?? false,
    billing: "api",
    label,
  };
}

const BASE = {
  configured: true,
  enabled: true,
  binary_path: "openclaw",
  binary_detected: null,
  version_pin: null,
  time_cap_min: null,
  concurrency: null,
  state_dir_root: null,
  brain_primary: "gemini",
  provider_slug: "google",
  model_override: null,
  sub_model_override: null,
  model_resolved: "google/gemini-3.1-pro-preview",
  mapping: [
    row("gemini", "Google Gemini", { active: true }),
    row("openrouter", "OpenRouter Picker"),
    row("hermes", "Hermes Agent Picker"),
    row("nvidia", "NVIDIA Picker", { ready: false }),
  ],
};

// The catalog's current_model is the CHAT brain's model, not the worker's.
const MODELS = {
  provider: "gemini",
  current_model: "gemini-3.5-flash",
  models: [
    { id: "gemini-3.5-flash", label: "Gemini 3.5 Flash" },
    { id: "gemini-3.1-pro-preview", label: "Gemini 3.1 Pro Preview" },
  ],
  source: "static",
  fetched_at: 0,
  selects: "model",
};

function stub(status: typeof BASE) {
  const fetchMock = vi.fn().mockImplementation(async (url: string) => {
    const u = String(url);
    if (u.includes("/api/jarvis-agent/status")) return { ok: true, json: async () => status };
    if (u.includes("/api/jarvis-agent/switch")) {
      return { ok: true, json: async () => ({ ok: true, active: "openrouter", restart_required: false }) };
    }
    if (u.includes("/models")) return { ok: true, json: async () => MODELS };
    return { ok: true, json: async () => ({}) };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function openPicker() {
  const trigger = await screen.findByLabelText("subagent_model.provider_label");
  fireEvent.click(trigger);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Assistant-Agents provider picker", () => {
  it("lists every registered worker by its own label, Hermes included", async () => {
    stub(BASE);
    render(<JarvisAgentSection hideHeader />);
    await openPicker();
    expect((await screen.findAllByText("OpenRouter Picker")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Hermes Agent Picker").length).toBeGreaterThan(0);
    expect(screen.getAllByText("subagent_model.needs_setup").length).toBeGreaterThan(0);
  });

  it("switches the worker when a ready provider is picked", async () => {
    const fetchMock = stub(BASE);
    render(<JarvisAgentSection hideHeader />);
    await openPicker();
    const options = await screen.findAllByText("OpenRouter Picker");
    fireEvent.click(options[options.length - 1]);
    await waitFor(() => {
      const post = fetchMock.mock.calls.find((c) =>
        String(c[0]).includes("/api/jarvis-agent/switch"),
      );
      expect(post).toBeDefined();
      expect(JSON.parse((post![1] as RequestInit).body as string).provider).toBe("openrouter");
    });
  });

  it("explains an unready provider instead of switching to it", async () => {
    const fetchMock = stub(BASE);
    render(<JarvisAgentSection hideHeader />);
    await openPicker();
    const options = await screen.findAllByText("NVIDIA Picker");
    fireEvent.click(options[options.length - 1]);
    expect(await screen.findByTestId("subagent-provider-unready")).toBeTruthy();
    expect(screen.getByText("subagent_model.set_up")).toBeTruthy();
    expect(
      fetchMock.mock.calls.some((c) => String(c[0]).includes("/api/jarvis-agent/switch")),
    ).toBe(false);
  });

  it("does not show the chat brain's model as the worker's model", async () => {
    stub(BASE);
    render(<JarvisAgentSection hideHeader />);
    const trigger = await screen.findByLabelText("apikeys_model.model_label");
    await waitFor(() => expect(trigger.textContent).not.toContain("Gemini 3.5 Flash"));
  });

  it("lists Hermes Agent's free models and saves the pick for hermes -m", async () => {
    const status = {
      ...BASE,
      brain_primary: "hermes",
      mapping: BASE.mapping.map((r) => ({ ...r, is_active_brain: r.jarvis === "hermes" })),
    };
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.includes("/api/jarvis-agent/status")) return { ok: true, json: async () => status };
      if (u.includes("/api/agent-chat/catalog")) {
        return {
          ok: true,
          json: async () => ({
            providers: [
              {
                id: "hermes",
                label: "Hermes Agent",
                curated_models: [
                  { id: "stepfun/step-3.7-flash:free", label: "Step 3.7 Flash", note: "free" },
                  { id: "poolside/laguna-s-2.1:free", label: "Laguna S 2.1", note: "free" },
                ],
              },
            ],
          }),
        };
      }
      if (u.includes("/api/jarvis-agent/model") && init?.method === "POST") {
        return { ok: true, json: async () => ({ ok: true, persisted: true, restart_required: true }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<JarvisAgentSection hideHeader />);
    fireEvent.click(await screen.findByLabelText("apikeys_model.model_label"));
    fireEvent.click(await screen.findByText("poolside/laguna-s-2.1:free"));
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(
        (c) => String(c[0]).includes("/api/jarvis-agent/model") && (c[1] as RequestInit)?.method === "POST",
      );
      expect(JSON.parse(String((post![1] as RequestInit).body)).model).toBe("poolside/laguna-s-2.1:free");
    });
    // Hermes is not a Brain provider: no request to a catalog it does not have.
    expect(
      fetchMock.mock.calls.some((c) => String(c[0]).includes("/api/providers/hermes/models")),
    ).toBe(false);
  });
});
