/**
 * The voice notice above a chat never switches the brain: the brain is picked
 * in one place, Settings › Model, and the notice links there. A recording
 * fake `fetch` proves no `POST /api/brain/switch` (or model save) leaves it.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { VoiceEngineDisplay } from "@/lib/voiceEngineDisplay";
import { useEventStore } from "@/store/events";
import { useSettingsPage } from "@/store/settingsPage";

// Identity translator; `fill` keeps its real substitution so names show up.
vi.mock("@/i18n", async () => {
  const actual = await vi.importActual<typeof import("@/i18n")>("@/i18n");
  return { useT: () => (key: string) => `${key}[{chat}|{voice}]`, fill: actual.fill };
});

let engine: VoiceEngineDisplay = { tier: "realtime", providerId: "gemini", providerLabel: "Gemini Live", model: "" };
vi.mock("@/hooks/useVoiceEngineDisplay", () => ({ useVoiceEngineDisplay: () => engine }));

import { VoiceContinuityNotice } from "./VoiceContinuityNotice";

/** Every request the notice sends, as "METHOD url". */
let sent: string[] = [];

beforeEach(() => {
  engine = { tier: "realtime", providerId: "gemini", providerLabel: "Gemini Live", model: "" };
  sent = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    sent.push(`${init?.method ?? "GET"} ${url}`);
    return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
  });
  useEventStore.setState({ activeSection: "chats" });
  useSettingsPage.setState({ page: null });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const hermes = { id: "hermes", label: "Hermes Agent", runner: "hermes-cli", model: "" };
const nous = { id: "nous", label: "Nous Portal", runner: "api", model: "stepfun/step-3.7-flash:free" };

const writes = () => sent.filter((line) => !line.startsWith("GET "));

describe("VoiceContinuityNotice", () => {
  it("explains why Hermes Agent cannot be the voice brain and changes nothing", () => {
    render(<VoiceContinuityNotice chat={hermes} />);
    const notice = screen.getByTestId("voice-continuity");
    expect(notice.getAttribute("data-kind")).toBe("cli");
    expect(notice.textContent).toContain("society.chat.voice_cli[Hermes Agent|Gemini Live]");
    expect(notice.textContent).toContain("society.chat.voice_cli_hermes");
    expect(screen.queryByTestId("voice-continuity-use")).toBeNull();

    fireEvent.click(screen.getByTestId("voice-continuity-settings"));
    expect(useEventStore.getState().activeSection).toBe("apikeys");
    expect(useSettingsPage.getState().page).toBe("model");
    expect(writes()).toEqual([]);
  });

  it("opens Settings › Model instead of switching the brain", () => {
    render(<VoiceContinuityNotice chat={nous} />);
    expect(screen.getByTestId("voice-continuity").getAttribute("data-kind")).toBe("switchable");
    expect(screen.getByTestId("voice-continuity").textContent).toContain(
      "society.chat.voice_differs_realtime[Nous Portal|Gemini Live]",
    );

    fireEvent.click(screen.getByTestId("voice-continuity-use"));

    expect(useEventStore.getState().activeSection).toBe("apikeys");
    expect(useSettingsPage.getState().page).toBe("model");
    expect(sent.some((line) => line.includes("/api/brain/switch"))).toBe(false);
    expect(writes()).toEqual([]);
  });

  it("leads the voice settings to the Voice page on Realtime", () => {
    render(<VoiceContinuityNotice chat={nous} />);
    fireEvent.click(screen.getByTestId("voice-continuity-settings"));
    expect(useSettingsPage.getState().page).toBe("voice");
    expect(writes()).toEqual([]);
  });

  it("says so when voice already runs on the chat's provider", () => {
    engine = { tier: "pipeline", providerId: "nous", providerLabel: "Nous Portal", model: "" };
    render(<VoiceContinuityNotice chat={nous} />);
    expect(screen.getByTestId("voice-continuity").getAttribute("data-kind")).toBe("same");
    expect(screen.queryByTestId("voice-continuity-use")).toBeNull();
  });
});
