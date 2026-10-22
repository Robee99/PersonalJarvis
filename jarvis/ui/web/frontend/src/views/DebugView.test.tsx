/**
 * The debug view shows who answers, read-only, and links to Settings › Model,
 * the one place the brain is chosen. A recording fake `fetch` proves it never
 * sends `POST /api/brain/switch`.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { _resetProvidersCacheForTests } from "@/hooks/useProviders";
import { useEventStore } from "@/store/events";
import { useSettingsPage } from "@/store/settingsPage";

import { DebugView } from "./DebugView";

let sent: string[] = [];

const PROVIDERS = {
  providers: [
    { id: "hermes", label: "Hermes Agent", tier: "brain", configured: true, active: false },
    { id: "gemini", label: "Google Gemini", tier: "brain", configured: true, active: true },
  ],
};

beforeEach(() => {
  sent = [];
  _resetProvidersCacheForTests();
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    sent.push(`${init?.method ?? "GET"} ${url}`);
    const body = url.startsWith("/api/providers") ? PROVIDERS : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  useEventStore.setState({ brainProvider: "gemini", brainModel: "gemini-3.5-flash", activeSection: "chats" });
  useSettingsPage.setState({ page: null });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("DebugView", () => {
  it("shows the current brain and its model without a switch", async () => {
    render(<DebugView />);
    await waitFor(() =>
      expect(screen.getByTestId("current-brain-value").textContent).toBe("Google Gemini · gemini-3.5-flash"),
    );
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByText("Apply")).toBeNull();
  });

  it("links to Settings › Model and never posts a brain switch", async () => {
    render(<DebugView />);
    fireEvent.click(screen.getByTestId("current-brain-change"));

    expect(useEventStore.getState().activeSection).toBe("apikeys");
    expect(useSettingsPage.getState().page).toBe("model");
    await waitFor(() => expect(sent.length).toBeGreaterThan(0));
    expect(sent.some((line) => line.includes("/api/brain/switch"))).toBe(false);
    expect(sent.filter((line) => !line.startsWith("GET "))).toEqual([]);
  });

  it("names Hermes and what it thinks with", () => {
    useEventStore.setState({ brainProvider: "hermes", brainModel: "local-qwen::qwen3-vl" });
    render(<DebugView />);
    expect(screen.getByTestId("current-brain-value").textContent).toContain("qwen3-vl");
  });
});
