/**
 * The fork's MODEL and VOICE faces on top of upstream's API Keys page
 * (P1-G): the brain picker leads the Model face, the route policy hides while
 * Hermes is the brain, and the Voice face offers the Pipeline when Realtime
 * is on (Hermes, Nous and a local Qwen have no live-audio model).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

const { voice } = vi.hoisted(() => ({
  voice: { mode: "pipeline" as string, setMode: vi.fn() },
}));

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
  useLocaleChunk: () => true,
}));
vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({ mode: voice.mode, setMode: voice.setMode }),
}));
vi.mock("@/hooks/useProviders", () => ({
  useProviders: () => ({ providers: [], loading: false, error: null, refetch: vi.fn(), setActiveOptimistic: vi.fn() }),
  useSectionHealth: () => ({ health: {} }),
}));
vi.mock("@/lib/providerFamilies", () => ({
  useProviderFamilies: () => ({ families: [], loading: false, error: null, checkedAt: null, reload: vi.fn() }),
}));
vi.mock("@/components/providers/BrainPicker", () => ({
  BrainPicker: () => <div data-testid="brain-picker" />,
}));
vi.mock("@/components/providers/ProviderTierSection", () => ({
  makeProviderCategories: () => ({ brain: {}, tts: {}, stt: {}, dictation: {} }),
  useTierHealth: () => ({}),
  ProviderCategory: ({ tier }: { tier: string }) => <div data-testid={`category-${tier}`} />,
}));
vi.mock("@/components/routing/RoutePolicyCard", () => ({
  RoutePolicyCard: () => <div data-testid="route-policy" />,
}));
vi.mock("@/views/settings/RealtimeVoiceGroup", () => ({
  RealtimeVoiceGroup: () => <div data-testid="engine-switch" />,
}));
vi.mock("@/components/providers/LiveProfile", () => ({
  LiveProfile: () => <div data-testid="live-profile" />,
}));
vi.mock("@/views/apikeys/KeyField", () => ({ KeyField: () => null }));
vi.mock("@/views/apikeys/AgentsTab", () => ({ AgentsTab: () => null }));
vi.mock("@/views/settings/JarvisApiGroup", () => ({ JarvisApiGroup: () => null }));

import { ApiKeysView } from "@/views/ApiKeysView";
import { useEventStore } from "@/store/events";
import { useSettingsPage } from "@/store/settingsPage";

beforeEach(() => {
  voice.mode = "pipeline";
  voice.setMode = vi.fn();
  useEventStore.setState({ brainProvider: "openai" });
  useSettingsPage.setState({ page: null });
});

afterEach(() => cleanup());

describe("ApiKeysView MODEL face", () => {
  it("leads with the brain picker and folds the full catalogue", () => {
    render(<ApiKeysView scope="model" />);
    expect(screen.getByTestId("brain-picker")).toBeTruthy();
    expect(screen.queryByTestId("category-brain")).toBeNull();
    fireEvent.click(screen.getByTestId("all-brain-providers-toggle"));
    expect(screen.getByTestId("category-brain")).toBeTruthy();
    expect(screen.getByTestId("route-policy")).toBeTruthy();
  });

  it("hides the route policy and says why while Hermes is the brain", () => {
    useEventStore.setState({ brainProvider: "hermes" });
    render(<ApiKeysView scope="model" />);
    expect(screen.queryByTestId("route-policy")).toBeNull();
    expect(screen.getByTestId("scope-note-model").textContent).toContain("settings_pages.hermes_note_model");
  });
});

describe("ApiKeysView VOICE face", () => {
  it("shows the Pipeline's speech providers and the engine switch", () => {
    render(<ApiKeysView scope="voice" />);
    expect(screen.getByTestId("engine-switch")).toBeTruthy();
    for (const tier of ["tts", "stt", "dictation"]) {
      expect(screen.getByTestId(`category-${tier}`)).toBeTruthy();
    }
    expect(screen.queryByTestId("live-profile")).toBeNull();
  });

  it("on Realtime offers the Pipeline for chat-only brains and opens the brain picker", () => {
    voice.mode = "realtime";
    render(<ApiKeysView scope="voice" />);
    expect(screen.getByTestId("live-profile")).toBeTruthy();
    fireEvent.click(screen.getByTestId("realtime-pipeline-hint-use"));
    expect(voice.setMode).toHaveBeenCalledWith("pipeline");
    expect(useSettingsPage.getState().page).toBe("model");
    expect(useEventStore.getState().activeSection).toBe("apikeys");
  });

  it("hides the engine switch with Hermes as the brain: voice always runs the Pipeline", () => {
    voice.mode = "realtime";
    useEventStore.setState({ brainProvider: "hermes" });
    render(<ApiKeysView scope="voice" />);
    expect(screen.queryByTestId("engine-switch")).toBeNull();
    expect(screen.queryByTestId("live-profile")).toBeNull();
    expect(screen.getByTestId("category-tts")).toBeTruthy();
  });
});
