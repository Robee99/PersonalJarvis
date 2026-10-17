import { render, screen } from "@testing-library/react";
import { act } from "react";
import { describe, expect, it, vi } from "vitest";
import {
  DEFAULT_GEV_EMBED_URL,
  GEV_EMBED_URL,
  resolveGeVEmbedUrl,
  WorldIntelligenceView,
} from "@/views/WorldIntelligenceView";

describe("WorldIntelligenceView", () => {
  it("embeds the local God's Eye View runtime with voice permission", () => {
    render(<WorldIntelligenceView />);

    const frame = screen.getByTitle("God’s Eye View") as HTMLIFrameElement;
    expect(frame.src).toBe(GEV_EMBED_URL);
    expect(new URL(frame.src).hostname).toBe("127.0.0.1");
    expect(new URL(frame.src).searchParams.get("embed")).toBe("1");
    expect(frame.getAttribute("allow")).toContain("microphone");
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(
      screen.getByRole("link", { name: /open separately/i }).getAttribute("href"),
    ).toBe(GEV_EMBED_URL);
  });

  it("allows configured loopback ports and rejects remote view servers", () => {
    expect(resolveGeVEmbedUrl()).toBe("http://127.0.0.1:4173/?embed=1");
    expect(resolveGeVEmbedUrl("http://localhost:4182/path?style=flir")).toBe(
      "http://localhost:4182/path?style=flir&embed=1",
    );
    expect(resolveGeVEmbedUrl("https://example.com/" )).toBeNull();
    expect(resolveGeVEmbedUrl("http://user:pass@127.0.0.1:4173/")).toBeNull();
    expect(DEFAULT_GEV_EMBED_URL).toBe("http://127.0.0.1:4173/");
  });

  it("waits for GEV readiness, forwards a requested view, and reports its acknowledgement", () => {
    render(<WorldIntelligenceView />);
    const frame = screen.getByTitle("God’s Eye View") as HTMLIFrameElement;
    const target = frame.contentWindow;
    if (!target) throw new Error("iframe has no content window");
    const postMessage = vi.spyOn(target, "postMessage");
    const origin = new URL(GEV_EMBED_URL!).origin;
    const view = { camera: { lat: 30, lon: -97, altitude_m: 1000 } };

    act(() => {
      window.dispatchEvent(new CustomEvent("jarvis:gev-view", { detail: { view } }));
    });
    expect(postMessage).not.toHaveBeenCalled();

    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: "gev:ready" },
          origin,
          source: target,
        }),
      );
    });
    expect(postMessage).toHaveBeenCalledTimes(1);
    expect(postMessage.mock.calls[0][0]).toMatchObject({ type: "gev:view", view });
    expect(postMessage.mock.calls[0][1]).toBe(origin);
    expect(screen.getByText(/Applying requested view/)).toBeTruthy();

    const request = postMessage.mock.calls[0][0] as { id: string };
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: "gev:view-applied", id: request.id, ok: true },
          origin,
          source: target,
        }),
      );
    });
    expect(screen.getByText(/View applied/)).toBeTruthy();
  });
});
