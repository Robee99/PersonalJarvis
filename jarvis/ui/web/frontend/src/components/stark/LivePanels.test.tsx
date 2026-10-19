import { fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveCameras, LiveNews } from "@/components/stark/LivePanels";
import { useI18nStore } from "@/i18n";
import { embedUrl, isPlayerError, type LiveStream } from "@/lib/live";
import { useIntelStore } from "@/store/intel";

function stream(key: string, name: string, kind: "news" | "camera", group = "", embed = `live_stream?channel=UC${key}`): LiveStream {
  return { key, name, short: name, kind, group, place: kind === "camera" ? `${name} view` : "", embed, page: `https://www.youtube.com/channel/UC${key}/live` };
}

const CATALOG = {
  streams: [
    stream("aljazeera", "Al Jazeera English", "news"),
    stream("sky", "Sky News", "news"),
    stream("euronews", "Euronews English", "news", "", "pykpO5kQJ98"),
    stream("jerusalem", "Jerusalem", "camera", "middle-east", "LMHUcDktP-w"),
    stream("istanbul", "Istanbul", "camera", "middle-east"),
    stream("hk", "Hong Kong", "camera", "asia"),
    stream("tokyo", "Tokyo", "camera", "asia"),
    stream("seoul", "Seoul", "camera", "asia"),
    stream("taipei", "Taipei", "camera", "asia"),
    stream("singapore", "Singapore", "camera", "asia"),
    stream("iss", "International Space Station", "camera", "space", "M3HKLzjvKPc"),
  ],
  groups: [
    { id: "middle-east", name: "Middle East" },
    { id: "asia", name: "Asia" },
    { id: "space", name: "Space" },
  ],
};

function render(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

function frames(): HTMLIFrameElement[] {
  return screen.queryAllByTestId("stark-live-frame") as HTMLIFrameElement[];
}

describe("live video panels", () => {
  beforeEach(() => {
    useI18nStore.getState().setUi("en", { push: false });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input) === "/api/intel/live" ? new Response(JSON.stringify(CATALOG), { status: 200 }) : new Response("{}", { status: 404 }),
      ),
    );
    try {
      window.localStorage.removeItem("world.live.playAll");
    } catch {
      // jsdom always has storage; nothing to clear otherwise.
    }
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    useIntelStore.setState({ watch: null });
  });

  it("builds a muted player address from a channel or a pinned video", () => {
    const byChannel = embedUrl(CATALOG.streams[1], true, "http://127.0.0.1:47821");
    expect(byChannel.startsWith("https://www.youtube-nocookie.com/embed/live_stream?channel=UCsky&")).toBe(true);
    const params = new URL(byChannel).searchParams;
    expect(params.get("autoplay")).toBe("1");
    expect(params.get("mute")).toBe("1");
    expect(params.get("origin")).toBe("http://127.0.0.1:47821");
    expect(embedUrl(CATALOG.streams[2], false).startsWith("https://www.youtube-nocookie.com/embed/pykpO5kQJ98?autoplay=0&")).toBe(true);
  });

  it("recognises a player error and nothing else", () => {
    expect(isPlayerError(JSON.stringify({ event: "onError", info: 150 }))).toBe(true);
    expect(isPlayerError(JSON.stringify({ event: "infoDelivery" }))).toBe(false);
    expect(isPlayerError("not json")).toBe(false);
    expect(isPlayerError({ event: "onError" })).toBe(false);
  });

  it("waits on the first news channel, then plays the one picked", async () => {
    render(<LiveNews />);
    await waitFor(() => expect(frames()).toHaveLength(1));
    expect(frames()[0].dataset.stream).toBe("aljazeera");
    expect(frames()[0].src).toContain("autoplay=0");
    fireEvent.click(screen.getByTestId("stark-live-channel-sky"));
    expect(frames()[0].dataset.stream).toBe("sky");
    expect(frames()[0].src).toContain("autoplay=1");
    expect(frames()[0].src).toContain("mute=1");
  });

  it("plays the channel the assistant asked for", async () => {
    render(<LiveNews />);
    await waitFor(() => expect(frames()).toHaveLength(1));
    act(() => useIntelStore.getState().applyCommand({ action: "watch", stream: "euronews" }));
    expect(frames()[0].dataset.stream).toBe("euronews");
    expect(frames()[0].src).toContain("/embed/pykpO5kQJ98?autoplay=1");
  });

  it("says when a stream is not live, from its own player only", async () => {
    render(<LiveNews />);
    await waitFor(() => expect(frames()).toHaveLength(1));
    const frame = frames()[0];
    const error = JSON.stringify({ event: "onError", info: 150 });
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data: error, origin: "https://evil.example", source: frame.contentWindow }));
    });
    expect(screen.queryByTestId("stark-live-offline")).toBeNull();
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data: error, origin: "https://www.youtube-nocookie.com", source: frame.contentWindow }));
    });
    const offline = screen.getByTestId("stark-live-offline");
    expect(offline.textContent).toContain("Al Jazeera English is not live right now.");
    expect(offline.querySelector("a")?.getAttribute("href")).toBe("https://www.youtube.com/channel/UCaljazeera/live");
  });

  it("shows four cameras of a region at a time and pages through the rest", async () => {
    render(<LiveCameras />);
    await waitFor(() => expect(frames()).toHaveLength(2));
    expect(frames().map((f) => f.dataset.stream)).toEqual(["jerusalem", "istanbul"]);
    expect(frames()[0].src).toContain("autoplay=0");
    fireEvent.click(screen.getByTestId("stark-live-group-asia"));
    expect(frames().map((f) => f.dataset.stream)).toEqual(["hk", "tokyo", "seoul", "taipei"]);
    fireEvent.click(screen.getByTestId("stark-live-next"));
    expect(frames().map((f) => f.dataset.stream)).toEqual(["singapore"]);
  });

  it("plays every camera on request and remembers it", async () => {
    render(<LiveCameras />);
    await waitFor(() => expect(frames()).toHaveLength(2));
    fireEvent.click(screen.getByTestId("stark-live-play-all"));
    expect(frames().every((f) => f.src.includes("autoplay=1"))).toBe(true);
    expect(window.localStorage.getItem("world.live.playAll")).toBe("1");
  });

  it("opens one camera large, by click or by the assistant", async () => {
    render(<LiveCameras />);
    await waitFor(() => expect(frames()).toHaveLength(2));
    fireEvent.click(screen.getByTestId("stark-live-expand-istanbul"));
    expect(frames().map((f) => f.dataset.stream)).toEqual(["istanbul"]);
    expect(frames()[0].src).toContain("autoplay=1");
    fireEvent.click(screen.getByTestId("stark-live-grid"));
    expect(frames()).toHaveLength(2);
    act(() => useIntelStore.getState().applyCommand({ action: "watch", stream: "iss" }));
    expect(frames().map((f) => f.dataset.stream)).toEqual(["iss"]);
    expect(screen.getByTestId("stark-live-group-space").getAttribute("aria-pressed")).toBe("true");
  });
});
