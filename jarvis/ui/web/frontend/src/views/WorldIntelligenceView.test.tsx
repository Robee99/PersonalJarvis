import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
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
});
