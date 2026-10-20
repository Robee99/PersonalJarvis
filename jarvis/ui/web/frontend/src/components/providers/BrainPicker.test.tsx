/**
 * The Model page's one list: Hermes decides, models on this computer, free cloud.
 * Pins that a local llama.cpp server is shown with its state and that one click
 * makes it Hermes's model, that the signed-in providers are one click from
 * answering directly, and that a server Hermes would refuse is not a choice.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { ProviderDescriptor } from "@/hooks/useProviders";
import { useEventStore } from "@/store/events";

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

const provider = (over: Partial<ProviderDescriptor>): ProviderDescriptor => ({
  id: "x", label: "X", tier: "brain", auth_mode: "api_key", secret_keys: [], secrets_set: {},
  dashboard_url: null, login_cli: null, install_hint: null, credential_path_hint: null,
  configured: true, active: false, cli_installed: null, credential_help: null, signup_url: null,
  billing: "api", alt_credential: null, ...over,
});

const PROVIDERS: ProviderDescriptor[] = [
  provider({ id: "hermes", label: "Hermes Agent" }),
  provider({ id: "local-openai", label: "Local server" }),
  provider({ id: "gemini", label: "Google Gemini", active: true }),
  provider({ id: "claude-cli", label: "Claude", billing: "subscription" }),
  provider({ id: "nokey", label: "No key", configured: false }),
  provider({ id: "voice", label: "Voice", tier: "tts" }),
];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useEventStore.getState().setBrainProvider("unknown");
  useEventStore.getState().setBrainModel("");
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

  it("says plainly who answers, and one click on Hermes Agent switches the brain", async () => {
    useEventStore.getState().setBrainProvider("local-openai");
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/api/brain/switch") return json({ ok: true });
      if (url.startsWith("/api/local-servers")) return json({ ...LIST, hermes_brain: false, selection: "" });
      return json(MODELS);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<BrainPicker />);

    const hermes = await screen.findByRole("radio", { name: /^Hermes Agent/ });
    expect(hermes.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByTestId("brain-status").textContent).toContain("local-openai");

    fireEvent.click(hermes);

    await waitFor(() => expect(hermes.getAttribute("aria-checked")).toBe("true"));
    expect(fetchMock).toHaveBeenCalledWith("/api/brain/switch", expect.objectContaining({ method: "POST" }));
    expect(screen.getByTestId("brain-status").textContent).toMatch(/Hermes is answering now/);
    expect(hermes.textContent).toContain("Active");
  });

  it("offers the signed-in providers under Jarvis directly and switches to one", async () => {
    useEventStore.getState().setBrainProvider("gemini");
    useEventStore.getState().setBrainModel("gemini-3.5-flash");
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/brain/switch") {
        calls.push(JSON.parse(String(init?.body)).provider);
        return json({ ok: true });
      }
      if (url.startsWith("/api/local-servers")) return json({ ...LIST, hermes_brain: false, selection: "" });
      return json(MODELS);
    }));
    const onSetUpProvider = vi.fn();
    render(<BrainPicker providers={PROVIDERS} onSetUpProvider={onSetUpProvider} />);

    expect(screen.getByTestId("brain-status").textContent).toContain("Google Gemini · gemini-3.5-flash");
    const gemini = await screen.findByRole("radio", { name: /^Google Gemini/ });
    expect(gemini.getAttribute("aria-checked")).toBe("true");
    // Hermes and the local-server card are rows of their own, not repeated here;
    // a provider with no key is reached through "Add a provider".
    expect(screen.queryByRole("radio", { name: /^No key/ })).toBeNull();
    expect(screen.queryByRole("radio", { name: /^Local server/ })).toBeNull();

    fireEvent.click(screen.getByRole("radio", { name: /^Claude/ }));
    await waitFor(() => expect(calls).toEqual(["claude-cli"]));
    await waitFor(() =>
      expect(screen.getByRole("radio", { name: /^Claude/ }).getAttribute("aria-checked")).toBe("true"),
    );

    fireEvent.click(screen.getByTestId("brain-picker-set-up"));
    expect(onSetUpProvider).toHaveBeenCalled();
  });

  it("keeps the direct providers folded while Hermes answers, until Jarvis directly is clicked", async () => {
    useEventStore.getState().setBrainProvider("hermes");
    vi.stubGlobal("fetch", vi.fn(async (url: string) => (
      url.startsWith("/api/local-servers") ? json(LIST) : json(MODELS)
    )));
    render(<BrainPicker providers={PROVIDERS} />);
    await screen.findByRole("radio", { name: /qwen.*11437/i });
    expect(screen.queryByRole("radio", { name: /^Claude/ })).toBeNull();

    fireEvent.click(screen.getByRole("radio", { name: /^Jarvis directly/ }));

    expect(screen.getByRole("radio", { name: /^Claude/ }).getAttribute("aria-checked")).toBe("false");
  });

  it("shows a local server too small for Hermes, with the reason, but not as a choice", async () => {
    useEventStore.getState().setBrainProvider("hermes");
    const list = {
      ...LIST,
      hermes_min_context: 64000,
      servers: [
        { ...LIST.servers[0], context: 131072 },
        {
          url: "http://127.0.0.1:11441", port: 11441, kind: "llama.cpp", state: "running",
          models: ["ui-venus-2-9b"], hermes_provider: "", hermes_model: "", context: 16384,
        },
      ],
    };
    const posted: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") posted.push(url);
      return url.startsWith("/api/local-servers") ? json(list) : json(MODELS);
    }));
    render(<BrainPicker />);

    const venus = (await screen.findByRole("radio", { name: /ui-venus-2-9b/ })) as HTMLButtonElement;
    expect(venus.disabled).toBe(true);
    expect(venus.textContent).toContain("Too small for Hermes: 16k context, it needs 64k");
    expect((screen.getByRole("radio", { name: /qwen.*11437/i }) as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(venus);
    expect(posted).toEqual([]);
  });

  it("follows a brain switch made elsewhere on the page", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => (
      url.startsWith("/api/local-servers") ? json({ ...LIST, hermes_brain: false, selection: "" }) : json(MODELS)
    )));
    useEventStore.getState().setBrainProvider("openrouter");
    render(<BrainPicker />);
    const hermes = await screen.findByRole("radio", { name: /^Hermes Agent/ });
    expect(hermes.getAttribute("aria-checked")).toBe("false");

    act(() => useEventStore.getState().setBrainProvider("hermes"));

    await waitFor(() => expect(hermes.getAttribute("aria-checked")).toBe("true"));
  });
});
