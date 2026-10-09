import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AssistantBrainPanel } from "./AssistantBrainPanel";
import type { ProviderDescriptor } from "@/hooks/useProviders";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function show(fail = false) {
  const changed = vi.fn();
  const voice = vi.fn();
  const fetch = vi.fn(async (_input, init?: RequestInit) => {
    if (init?.method === "PUT") {
      return new Response(JSON.stringify(fail ? { detail: "Activation unavailable" } : {
        ok: true, provider: "hermes", model: "local-qwen::qwen", persisted: true,
        applied_live: true, restart_required: false, probe: null,
      }), { status: fail ? 503 : 200 });
    }
    return new Response(JSON.stringify({ provider: "hermes", current_model: "hermes-agent", source: "static", models: [
      { id: "hermes-agent", label: "Hermes decides" },
      { id: "local-qwen::qwen", label: "Qwen3.6 35B A3B (local llama.cpp)" },
    ] }));
  });
  vi.stubGlobal("fetch", fetch);
  const providers = [
    { id: "local-openai", tier: "brain", active: true, label: "Previous brain" },
    { id: "nemotron-local", tier: "stt", active: true, label: "Nemotron" },
    { id: "piper", tier: "tts", active: true, label: "Piper" },
  ] as ProviderDescriptor[];
  render(<AssistantBrainPanel providers={providers} onChanged={changed} onVoice={voice} />);
  return { changed, voice, fetch };
}

describe("one Jarvis model control", () => {
  it("saves and activates the existing local Qwen in one gesture", async () => {
    const fixture = show();
    await screen.findByText("Hermes decides");
    fireEvent.click(screen.getByRole("button", { name: "Model" }));
    fireEvent.click(screen.getByText("Qwen3.6 35B A3B (local llama.cpp)"));
    await waitFor(() => expect(fixture.changed).toHaveBeenCalledOnce());
    const write = fixture.fetch.mock.calls.find((call) => call[1]?.method === "PUT")!;
    expect(write[0]).toBe("/api/providers/hermes/model");
    expect(JSON.parse(String(write[1]?.body))).toEqual({ model: "local-qwen::qwen", persist: true, activate: true });
  });

  it("shows the current voice services and opens their settings without switching the brain", async () => {
    const fixture = show();
    fireEvent.click(screen.getByRole("button", { name: /Nemotron/ }));
    fireEvent.click(screen.getByRole("button", { name: /Piper/ }));
    expect(fixture.voice.mock.calls).toEqual([["stt"], ["tts"]]);
    expect(fixture.fetch.mock.calls.some((call) => call[1]?.method === "PUT")).toBe(false);
  });

  it("does not report activation or replace the displayed pick on failure", async () => {
    const fixture = show(true);
    await screen.findByText("Hermes decides");
    fireEvent.click(screen.getByRole("button", { name: "Model" }));
    fireEvent.click(screen.getByText("Qwen3.6 35B A3B (local llama.cpp)"));
    await waitFor(() => expect(fixture.fetch.mock.calls.some((call) => call[1]?.method === "PUT")).toBe(true));
    await screen.findByText("Hermes decides");
    expect(fixture.changed).not.toHaveBeenCalled();
  });
});
