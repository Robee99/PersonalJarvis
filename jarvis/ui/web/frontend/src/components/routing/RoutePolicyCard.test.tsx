import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ProviderDescriptor } from "@/hooks/useProviders";
import type { RoutePolicy } from "@/lib/routePolicyApi";

vi.mock("@/i18n", async () => {
  const actual = await vi.importActual<typeof import("@/i18n")>("@/i18n");
  return { useT: () => (key: string) => key, fill: actual.fill };
});

const pushToast = vi.fn();
let events: { name: string; payload?: unknown }[] = [];
vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: object) => unknown) => selector({ pushToast, events }),
}));

import { RoutePolicyCard } from "./RoutePolicyCard";

const OFF: RoutePolicy = {
  enabled: false,
  fast: { provider: "", model: null, local: false },
  deep: { provider: "", model: null, local: false },
  escalation: {
    enabled: false, via: "paperclip", agent: "", trigger_phrases: [],
    deadline_s: 180, poll_interval_s: 3, max_per_session: 5, max_context_chars: 4000,
  },
  deny_providers: [],
  deny_model_prefixes: [],
  allow_cloud_vision: false,
};

const providers = [
  { id: "nous", label: "Nous Portal", tier: "brain", billing: "api", configured: true },
  { id: "local-openai", label: "Local server (OpenAI-compatible)", tier: "brain", billing: "local", configured: true },
  { id: "gemini-live", label: "Gemini Live", tier: "realtime", billing: "api", configured: true },
] as unknown as ProviderDescriptor[];

function stubFetch(view = { policy: OFF, can_restore: false }) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    if (init?.method === "PUT") {
      const patch = JSON.parse(String(init.body));
      return { ok: true, json: async () => ({ ok: true, policy: { ...OFF, ...patch, escalation: { ...OFF.escalation, ...patch.escalation } }, can_restore: true }) };
    }
    if (init?.method === "POST") return { ok: true, json: async () => ({ ok: true, policy: OFF, can_restore: true }) };
    return { ok: true, json: async () => view };
  }));
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  events = [];
});

describe("RoutePolicyCard", () => {
  it("starts off, with cloud vision off and escalation off", async () => {
    stubFetch();
    render(<RoutePolicyCard providers={providers} />);
    const enabled = await screen.findByTestId("route-policy-enabled");
    expect(enabled.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByTestId("route-policy-cloud-vision").getAttribute("aria-checked")).toBe("false");
    expect(screen.getByTestId("route-policy-escalation").getAttribute("aria-checked")).toBe("false");
    expect((screen.getByTestId("route-policy-save") as HTMLButtonElement).disabled).toBe(true);
  });

  it("lists Brain providers only and marks a local server as on this computer", async () => {
    stubFetch();
    render(<RoutePolicyCard providers={providers} />);
    fireEvent.click(await screen.findByTestId("route-policy-deep-provider"));
    expect(await screen.findByText("Local server (OpenAI-compatible)")).toBeTruthy();
    expect(screen.queryByText("Gemini Live")).toBeNull();
    fireEvent.click(screen.getByText("Local server (OpenAI-compatible)"));
    // Picking a local server ticks "runs on this computer" by itself.
    await waitFor(() =>
      expect(screen.getByTestId("route-policy-deep-local").getAttribute("aria-checked")).toBe("true"),
    );
  });

  it("saves Step fast and local Qwen deep in one PUT", async () => {
    const calls = stubFetch({
      policy: {
        ...OFF,
        fast: { provider: "nous", model: "stepfun/step-3.7-flash:free", local: false },
        deep: { provider: "local-openai", model: "qwen", local: true },
      },
      can_restore: false,
    });
    render(<RoutePolicyCard providers={providers} />);
    fireEvent.click(await screen.findByTestId("route-policy-enabled"));
    fireEvent.click(screen.getByTestId("route-policy-save"));
    await waitFor(() => expect(calls.some((c) => c.init?.method === "PUT")).toBe(true));
    const body = JSON.parse(String(calls.find((c) => c.init?.method === "PUT")!.init!.body));
    expect(body.enabled).toBe(true);
    expect(body.deep).toEqual({ provider: "local-openai", model: "qwen", local: true });
    expect(body.fast.provider).toBe("nous");
    expect(body.allow_cloud_vision).toBe(false);
    expect(body.escalation.enabled).toBe(false);
    await waitFor(() => expect(screen.getByTestId("route-policy-restore")).toBeTruthy());
  });

  it("shows what the last turn ran on, why, and what was skipped", async () => {
    stubFetch();
    events = [
      { name: "VoiceSessionStarted" },
      {
        name: "BrainRouteSelected",
        payload: {
          tier: "deep", reason: "deep-intent", intent_level: "code",
          chain: ["local-openai:qwen", "nous:stepfun/step-3.7-flash:free"],
          excluded: ["deep:claude-api:denied"], outcome: "", elapsed_ms: 2,
        },
      },
    ];
    render(<RoutePolicyCard providers={providers} />);
    const last = await screen.findByTestId("route-policy-last");
    expect(last.textContent).toContain("local-openai:qwen");
    expect(last.textContent).toContain("deep-intent");
    expect(last.textContent).toContain("nous:stepfun/step-3.7-flash:free");
    expect(last.textContent).toContain("deep:claude-api:denied");
  });
});
