/**
 * Component tests for the voice section's "API Keys" tab.
 *
 * The tab holds no provider picker of its own any more: speech-to-text and
 * the optional wording pass are picked in Settings › Voice — one control per
 * config. These tests pin
 * that the tab links to both, renders no second picker, leaks no other tier,
 * and brings none of the API-Keys view's own category tabs along.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import type { ProviderDescriptor } from "@/hooks/useProviders";

// Deterministic data: no network round-trip, no react-query provider needed.
const STT_PROVIDER: ProviderDescriptor = {
  id: "test-speech",
  label: "Test Speech Service",
  tier: "stt",
  auth_mode: "api_key",
  secret_keys: ["TEST_SPEECH_API_KEY"],
  secrets_set: { TEST_SPEECH_API_KEY: false },
  dashboard_url: null,
  login_cli: null,
  install_hint: null,
  credential_path_hint: null,
  configured: false,
  active: false,
  cli_installed: null,
  credential_help: null,
  signup_url: null,
  billing: "api",
  alt_credential: null,
};

const BRAIN_PROVIDER: ProviderDescriptor = {
  ...STT_PROVIDER,
  id: "test-brain",
  label: "Test Brain Service",
  tier: "brain",
  secret_keys: ["TEST_BRAIN_API_KEY"],
  secrets_set: { TEST_BRAIN_API_KEY: false },
};

// The optional wording tier. `optional: true` is the whole point: a missing key
// here changes nothing about how dictation behaves, so the card must never read
// like unfinished setup.
const DICTATION_PROVIDER: ProviderDescriptor = {
  ...STT_PROVIDER,
  id: "test-wording",
  label: "Test Wording Service",
  tier: "dictation",
  secret_keys: ["TEST_WORDING_API_KEY"],
  secrets_set: { TEST_WORDING_API_KEY: false },
  optional: true,
};

vi.mock("@/hooks/useProviders", () => ({
  sectionHealthForSubject: (
    health: { subject_id?: string } | undefined,
    subjectId?: string,
  ) => (subjectId && health?.subject_id === subjectId ? health : undefined),
  useProviders: () => ({
    providers: [STT_PROVIDER, BRAIN_PROVIDER, DICTATION_PROVIDER],
    loading: false,
    error: null,
    refetch: vi.fn(),
    setActiveOptimistic: vi.fn(),
  }),
  useSectionHealth: () => ({ health: {} }),
}));

import { VoiceApiKeysTab } from "@/views/voice/VoiceApiKeysTab";
import { useEventStore } from "@/store/events";
import { useSettingsPage } from "@/store/settingsPage";
import { requestedApiKeysTab } from "@/lib/apiKeysTab";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("VoiceApiKeysTab", () => {
  // Since 2026-10-11 the tab holds no picker: speech-to-text has ONE place,
  // Settings › Voice, and so does the wording pass.
  // A second picker here wrote the same config as its twin (UI audit P0-3).
  it("links to the one place speech-to-text is picked instead of rendering a second picker", () => {
    render(<VoiceApiKeysTab />);

    expect(screen.getByTestId("voice-api-keys-tab")).toBeTruthy();
    expect(screen.getByTestId("voice-api-keys-stt-link")).toBeTruthy();
    expect(screen.queryByText("Test Speech Service")).toBeNull();
    expect(screen.queryByLabelText("Enter TEST_SPEECH_API_KEY")).toBeNull();
  });

  it("links to the wording pass on Settings › Voice instead of rendering it again", () => {
    render(<VoiceApiKeysTab />);

    expect(screen.getByTestId("voice-api-keys-wording-link")).toBeTruthy();
    expect(screen.queryByText("Test Wording Service")).toBeNull();
  });

  it("opens Settings › Voice on the speech-to-text tab", () => {
    render(<VoiceApiKeysTab />);

    fireEvent.click(within(screen.getByTestId("voice-api-keys-stt-link")).getByRole("button"));

    expect(useEventStore.getState().activeSection).toBe("apikeys");
    expect(useSettingsPage.getState().page).toBe("voice");
    expect(requestedApiKeysTab()).toBe("stt");
  });

  it("opens Settings › Voice on the wording tab", () => {
    render(<VoiceApiKeysTab />);

    fireEvent.click(within(screen.getByTestId("voice-api-keys-wording-link")).getByRole("button"));

    expect(useSettingsPage.getState().page).toBe("voice");
    expect(requestedApiKeysTab()).toBe("dictation");
  });

  it("shows only the speech tiers, never other tiers' providers", () => {
    render(<VoiceApiKeysTab />);

    expect(screen.queryByText("Test Brain Service")).toBeNull();
  });

  it("brings none of the API-Keys view's category tabs along", () => {
    render(<VoiceApiKeysTab />);

    expect(screen.queryByTestId("api-keys-category-tabs")).toBeNull();
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
  });

  // The Realtime|Pipeline switch belongs to the API-Keys view, where the copy
  // that explains it lives too. Repeating it here put an engine-wide decision
  // in a section that only asks about speech-to-text providers.
  it("leaves the voice-engine switch to the API-Keys view", () => {
    render(<VoiceApiKeysTab />);

    expect(screen.queryByTestId("voice-engine-header-control")).toBeNull();
    expect(screen.queryByTestId("voice-engine-pick-one-hint")).toBeNull();
  });

  it("stands its own header down when the merged section owns it", () => {
    const { rerender } = render(<VoiceApiKeysTab />);
    const withHeader = screen.getAllByRole("heading").length;

    rerender(<VoiceApiKeysTab hideHeader />);
    expect(screen.getAllByRole("heading").length).toBe(withHeader - 1);
  });
});
