import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { VoiceEngineDisplay } from "@/lib/voiceEngineDisplay";

// Identity translator; `fill` keeps its real substitution so names show up.
vi.mock("@/i18n", async () => {
  const actual = await vi.importActual<typeof import("@/i18n")>("@/i18n");
  return { useT: () => (key: string) => `${key}[{chat}|{voice}]`, fill: actual.fill };
});

let engine: VoiceEngineDisplay = { tier: "realtime", providerId: "gemini", providerLabel: "Gemini Live", model: "" };
vi.mock("@/hooks/useVoiceEngineDisplay", () => ({ useVoiceEngineDisplay: () => engine }));

const setModeAsync = vi.fn(async () => undefined);
vi.mock("@/hooks/useVoiceMode", () => ({ useVoiceMode: () => ({ setModeAsync }) }));

const switchBrainProvider = vi.fn(async (_id: string) => undefined);
const saveBrainProviderModel = vi.fn(async (_id: string, _model: string) => ({}));
vi.mock("@/hooks/useProviders", () => ({
  switchBrainProvider: (id: string) => switchBrainProvider(id),
  saveBrainProviderModel: (id: string, model: string) => saveBrainProviderModel(id, model),
}));

const requestApiKeysTab = vi.fn();
vi.mock("@/lib/apiKeysTab", () => ({ requestApiKeysTab: (tab: string) => requestApiKeysTab(tab) }));

const pushToast = vi.fn();
const setActiveSection = vi.fn();
vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: object) => unknown) => selector({ pushToast, setActiveSection }),
}));

import { VoiceContinuityNotice } from "./VoiceContinuityNotice";

beforeEach(() => {
  engine = { tier: "realtime", providerId: "gemini", providerLabel: "Gemini Live", model: "" };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const hermes = { id: "hermes", label: "Hermes Agent", runner: "hermes-cli", model: "" };
const nous = { id: "nous", label: "Nous Portal", runner: "api", model: "stepfun/step-3.7-flash:free" };

describe("VoiceContinuityNotice", () => {
  it("explains why Hermes Agent cannot be the voice brain and changes nothing", () => {
    render(<VoiceContinuityNotice chat={hermes} />);
    const notice = screen.getByTestId("voice-continuity");
    expect(notice.getAttribute("data-kind")).toBe("cli");
    expect(notice.textContent).toContain("society.chat.voice_cli[Hermes Agent|Gemini Live]");
    expect(notice.textContent).toContain("society.chat.voice_cli_hermes");
    expect(screen.queryByTestId("voice-continuity-use")).toBeNull();

    fireEvent.click(screen.getByTestId("voice-continuity-settings"));
    expect(requestApiKeysTab).toHaveBeenCalledWith("brain");
    expect(setActiveSection).toHaveBeenCalledWith("apikeys");
    expect(switchBrainProvider).not.toHaveBeenCalled();
    expect(setModeAsync).not.toHaveBeenCalled();
  });

  it("switches nothing on render, only after an explicit click", async () => {
    render(<VoiceContinuityNotice chat={nous} />);
    expect(screen.getByTestId("voice-continuity").getAttribute("data-kind")).toBe("switchable");
    expect(screen.getByTestId("voice-continuity").textContent).toContain(
      "society.chat.voice_differs_realtime[Nous Portal|Gemini Live]",
    );
    expect(switchBrainProvider).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("voice-continuity-use"));
    await waitFor(() => expect(setModeAsync).toHaveBeenCalledWith("pipeline"));
    expect(switchBrainProvider).toHaveBeenCalledWith("nous");
    expect(saveBrainProviderModel).toHaveBeenCalledWith("nous", "stepfun/step-3.7-flash:free");
    expect(pushToast).toHaveBeenCalledWith("success", expect.stringContaining("Nous Portal"));
  });

  it("leaves the voice mode alone when the brain refuses the provider", async () => {
    switchBrainProvider.mockRejectedValueOnce(new Error("Nous Portal needs a key."));
    render(<VoiceContinuityNotice chat={nous} />);
    fireEvent.click(screen.getByTestId("voice-continuity-use"));
    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith("error", expect.stringContaining("Nous Portal needs a key.")),
    );
    expect(setModeAsync).not.toHaveBeenCalled();
    expect(saveBrainProviderModel).not.toHaveBeenCalled();
  });

  it("keeps Pipeline as it is when only the brain differs", async () => {
    engine = { tier: "pipeline", providerId: "gemini", providerLabel: "Google Gemini", model: "gemini-3.5-flash" };
    render(<VoiceContinuityNotice chat={{ ...nous, model: "" }} />);
    fireEvent.click(screen.getByTestId("voice-continuity-use"));
    await waitFor(() => expect(switchBrainProvider).toHaveBeenCalledWith("nous"));
    await waitFor(() => expect(pushToast).toHaveBeenCalled());
    expect(setModeAsync).not.toHaveBeenCalled();
    expect(saveBrainProviderModel).not.toHaveBeenCalled();
  });

  it("says so when voice already runs on the chat's provider", () => {
    engine = { tier: "pipeline", providerId: "nous", providerLabel: "Nous Portal", model: "" };
    render(<VoiceContinuityNotice chat={nous} />);
    expect(screen.getByTestId("voice-continuity").getAttribute("data-kind")).toBe("same");
    expect(screen.queryByTestId("voice-continuity-use")).toBeNull();
  });
});
