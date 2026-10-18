import { render as rtlRender, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_GEV_EMBED_URL,
  GEV_EMBED_URL,
  resolveGeVEmbedUrl,
  WorldIntelligenceView,
} from "@/views/WorldIntelligenceView";
import { ago } from "@/components/stark/WorldPanels";
import { useI18nStore } from "@/i18n";

const NEWS = {
  items: [
    { title: "Newest headline", link: "https://example.org/a", source: "BBC World", published: 1_700_000_000 },
    { title: "Older headline", link: "https://example.org/b", source: "NPR World", published: 1_699_990_000 },
  ],
  sources: ["BBC World", "Al Jazeera", "NPR World"],
  failed: ["Al Jazeera"],
  fetched_at: 1_700_000_100,
};

function render(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

describe("WorldIntelligenceView", () => {
  beforeEach(() => {
    useI18nStore.getState().setUi("en", { push: false });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input) === "/api/deck/news"
          ? new Response(JSON.stringify(NEWS), { status: 200 })
          : new Response("{}", { status: 404 }),
      ),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("shows the news broadcast, the feeds that answered and the ticker", async () => {
    render(<WorldIntelligenceView />);
    const items = await screen.findAllByTestId("stark-news-item");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("Newest headline");
    expect(items[0].querySelector("a")?.getAttribute("href")).toBe("https://example.org/a");
    expect(items[0].querySelector("a")?.getAttribute("rel")).toContain("noopener");
    expect(screen.getAllByTestId("stark-feed-up")).toHaveLength(2);
    expect(screen.getByTestId("stark-feed-down").textContent).toContain("Al Jazeera");
    expect(screen.getByTestId("stark-ticker").textContent).toContain("Older headline");
  });

  it("says how old a story is", () => {
    const now = 1_700_000_000;
    expect(ago(null, now)).toBe("—");
    expect(ago(now - 30, now)).toBe("now");
    expect(ago(now - 12 * 60, now)).toBe("12m");
    expect(ago(now - 3 * 3600, now)).toBe("3h");
    expect(ago(now - 2 * 86_400, now)).toBe("2d");
  });

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
