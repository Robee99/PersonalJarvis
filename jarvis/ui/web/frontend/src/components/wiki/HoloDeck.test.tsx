import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { HoloDeckButton } from "@/components/wiki/HoloDeck";
import { useEventStore } from "@/store/events";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stubInstall(status: number, body: unknown) {
  const calls: Array<[string, RequestInit | undefined]> = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    calls.push([url, init]);
    return new Response(JSON.stringify(body), { status });
  });
  return calls;
}

describe("HoloDeckButton", () => {
  it("installs the tracking files, then shows the deck in a camera-enabled frame", async () => {
    const calls = stubInstall(200, { ready: true });
    render(<HoloDeckButton />);

    fireEvent.click(screen.getByTestId("wiki-holo-open"));

    const overlay = await screen.findByTestId("wiki-holo-overlay");
    expect(calls).toEqual([["/api/holo/install", { method: "POST" }]]);
    const frame = overlay.querySelector("iframe");
    expect(frame?.getAttribute("src")).toBe("/holo/");
    expect(frame?.getAttribute("allow")).toContain("camera");

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("wiki-holo-overlay")).toBeNull());
  });

  it("reports a failed download and never opens the deck", async () => {
    stubInstall(502, { ready: false, error: "Downloading HOLO failed: offline" });
    const toasts: string[] = [];
    useEventStore.setState({ pushToast: (_kind: string, message: string) => toasts.push(message) } as never);
    render(<HoloDeckButton />);

    fireEvent.click(screen.getByTestId("wiki-holo-open"));

    await waitFor(() => expect(toasts).toHaveLength(1));
    expect(toasts[0]).toContain("Downloading HOLO failed: offline");
    expect(screen.queryByTestId("wiki-holo-overlay")).toBeNull();
    expect((screen.getByTestId("wiki-holo-open") as HTMLButtonElement).disabled).toBe(false);
  });
});
