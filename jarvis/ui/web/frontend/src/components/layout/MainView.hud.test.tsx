import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useEffect } from "react";
import { MainView } from "./MainView";
import { useEventStore, type SectionId } from "@/store/events";

/**
 * The HUD is ONE section (2026-10-12): the chat, the HUD and the memory orb
 * used to be three pages. Their three ids all stay valid — deep links, voice
 * navigation ("open chat", "open the memory orb") and the navigate parity
 * tests name them — and all three land on the same view, without
 * remounting it when one of them is opened while another is on screen.
 */
const mounts = vi.fn();
vi.mock("@/views/ChatsSurface", () => ({
  ChatsSurface: () => {
    useEffect(() => {
      mounts();
    }, []);
    return <div data-testid="hud-surface" />;
  },
}));

beforeEach(() => {
  mounts.mockClear();
  useEventStore.setState({ activeSection: "chats", solo: false, detachedViews: [] });
});

afterEach(cleanup);

describe("MainView — chat, HUD and memory orb are one view", () => {
  it.each(["chats", "deck", "orb"] as SectionId[])("renders the HUD for %s", async (activeSection) => {
    useEventStore.setState({ activeSection });
    render(<MainView />);
    expect(await screen.findByTestId("hud-surface")).toBeTruthy();
  });

  it("moving between the three ids keeps the same HUD mounted", async () => {
    render(<MainView />);
    await screen.findByTestId("hud-surface");
    act(() => useEventStore.setState({ activeSection: "orb" }));
    act(() => useEventStore.setState({ activeSection: "deck" }));
    act(() => useEventStore.setState({ activeSection: "chats" }));
    expect(screen.getByTestId("hud-surface")).toBeTruthy();
    expect(mounts).toHaveBeenCalledTimes(1);
  });
});
