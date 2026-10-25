import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { VisionControls } from "./VisionControls";
import { getLiveVision } from "@/lib/liveVision";

const chat = vi.hoisted(() => ({ activeSessionId: "chat-a", activeSession: null,
  draft: { provider: "local-openai", model: "vision" }, busy: false, timeline: { items: [] }, send: vi.fn(async () => "sent") }));
vi.mock("@/store/agentChat", () => ({ useAgentChatStore: Object.assign(
  (selector: (state: typeof chat) => unknown) => selector(chat), { getState: () => chat }) }));
const events = vi.hoisted(() => ({ voiceState: "idle", brainModel: "gemma-4b-qat", brainProvider: "local-openai",
  assistantName: "Jarvis", activeSection: "chats", setActiveSection: () => {} }));
vi.mock("@/store/events", () => ({ useEventStore: Object.assign(
  (selector: (state: typeof events) => unknown) => selector(events), { getState: () => events }) }));
const render = (ui: ReactElement) => rtlRender(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);

function stream() {
  const track = { stop: vi.fn(), addEventListener: vi.fn(), getSettings: () => ({ displaySurface: "monitor" }) };
  return { getTracks: () => [track], getVideoTracks: () => [track], track };
}
let camera: ReturnType<typeof stream>;
let display: ReturnType<typeof stream>;
let media: { getUserMedia: ReturnType<typeof vi.fn>; getDisplayMedia: ReturnType<typeof vi.fn> };
beforeEach(() => {
  camera = stream(); display = stream();
  media = { getUserMedia: vi.fn(async () => camera), getDisplayMedia: vi.fn(async () => display) };
  vi.stubGlobal("navigator", { mediaDevices: media });
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(
    url.endsWith("capabilities") ? { tracking_ready: false, detail: "Local landmark assets unavailable" }
      : url.endsWith("watch-indicator") ? { available: true }
      : { text: "My ears are off", queued: false }), { status: 200 })));
  history.replaceState(null, "", "/?mute=1");
});
afterEach(() => { cleanup(); getLiveVision().dispose(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("the real eyes controls", () => {
  it("does not request either device on startup, and operates them independently", async () => {
    render(<VisionControls />);
    expect(media.getUserMedia).not.toHaveBeenCalled();
    expect(media.getDisplayMedia).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("rail-eyes"));
    await waitFor(() => expect(screen.getByTestId("rail-eyes").getAttribute("aria-pressed")).toBe("true"));
    expect(media.getUserMedia).toHaveBeenCalledWith({ video: { facingMode: "user" }, audio: false });
    fireEvent.click(screen.getByRole("button", { name: "Share screen" }));
    await screen.findByRole("button", { name: "Stop sharing" });
    fireEvent.click(screen.getByTestId("rail-eyes"));
    expect(camera.track.stop).toHaveBeenCalledOnce();
    expect(display.track.stop).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Stop sharing" }).getAttribute("aria-pressed")).toBe("true");
  });
  it("reports unavailable local posture tracking while leaving webcam vision usable", async () => {
    render(<VisionControls />); fireEvent.click(screen.getByTestId("rail-eyes"));
    await screen.findByText("Local landmark assets unavailable");
    expect(getLiveVision().state.webcam).toBe("on");
    const notices = vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith("/notice"));
    expect(JSON.parse(String(notices[0][1]?.body)).silent).toBe(true);
  });
  it("releases both sources when the controls unmount", async () => {
    const view = render(<VisionControls />); fireEvent.click(screen.getByTestId("rail-eyes"));
    await waitFor(() => expect(getLiveVision().state.webcam).toBe("on"));
    fireEvent.click(screen.getByRole("button", { name: "Share screen" }));
    await waitFor(() => expect(getLiveVision().state.screen).toBe("on"));
    view.unmount();
    expect(camera.track.stop).toHaveBeenCalledOnce(); expect(display.track.stop).toHaveBeenCalledOnce();
  });
});
