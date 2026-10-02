import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

vi.mock("framer-motion", async () => {
  const actual = await vi.importActual<typeof import("framer-motion")>("framer-motion");
  return { ...actual, useReducedMotion: () => true };
});

import { DeckOrb } from "@/components/deck/DeckOrb";
import { readDeckAvatar, writeDeckAvatar } from "@/lib/deckAvatar";
import { useEventStore } from "@/store/events";

describe("deck avatar", () => {
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
    useEventStore.setState({ voiceState: "idle" });
  });

  test("Gigi is the default and a broken value falls back to it", () => {
    expect(readDeckAvatar()).toBe("gigi");
    window.localStorage.setItem("deck.avatar.v1", "toaster");
    expect(readDeckAvatar()).toBe("gigi");
  });

  test("switching to the reactor swaps the centre and is remembered", () => {
    const { container } = render(<DeckOrb steps={[]} busy={false} />);
    expect(screen.getByTestId("jarvis-orb")).toBeTruthy();

    act(() => writeDeckAvatar("reactor"));

    expect(screen.queryByTestId("jarvis-orb")).toBeNull();
    expect(screen.getByTestId("deck-reactor")).toBeTruthy();
    expect(window.localStorage.getItem("deck.avatar.v1")).toBe("reactor");
    // Ten coil blocks, and no scenery lines in the reticle.
    expect(container.querySelectorAll(".deck-reactor-coils path")).toHaveLength(10);
    expect(container.querySelectorAll("line")).toHaveLength(0);
  });

  test("the coils carry the voice state so they turn only in the voice", () => {
    writeDeckAvatar("reactor");
    const { container } = render(<DeckOrb steps={[]} busy={false} />);
    const coils = () => container.querySelector(".deck-reactor-coils");
    expect(coils()?.getAttribute("data-voice")).toBe("idle");

    act(() => useEventStore.setState({ voiceState: "speaking" }));

    expect(coils()?.getAttribute("data-voice")).toBe("speaking");
  });
});
