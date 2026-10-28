import { act, cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The voice turn in flight under the HUD's chat thread, driven through the
 * REAL WebSocket path: envelope → useWebSocket → event/home stores → tail.
 * What is pinned is the sequence a spoken turn produces on the bus — partial
 * transcripts while the person talks, the final transcript, the reply
 * streaming, the spoken line — that each stage replaces the one before
 * instead of stacking up, and that a turn the backend filed into the chat
 * leaves the tail (one list, never the same words twice).
 */

import { useWebSocket } from "@/hooks/useWebSocket";
import { useI18nStore } from "@/i18n";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { useAgentChatStore } from "@/store/agentChat";
import type { TimelineItem } from "@/components/agentchat/reduce";
import { HudVoiceTail, hudTranscriptView, useHudVoiceTail } from "@/components/deck/HudTranscriptPanel";

class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  static last: MockWebSocket | null = null;
  readyState = MockWebSocket.OPEN;
  private listeners: Record<string, Array<(ev: unknown) => void>> = {};
  url: string;
  constructor(url: string) {
    this.url = url;
    MockWebSocket.last = this;
    queueMicrotask(() => this.fire("open", {}));
  }
  addEventListener(type: string, fn: (ev: unknown) => void) {
    (this.listeners[type] ??= []).push(fn);
  }
  send = vi.fn();
  close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED;
    this.fire("close", {});
  });
  fire(type: string, ev: unknown) {
    (this.listeners[type] ?? []).forEach((fn) => fn(ev));
  }
  deliver(eventName: string, payload: Record<string, unknown>) {
    this.fire("message", {
      data: JSON.stringify({
        type: "event",
        event_name: eventName,
        source_layer: "speech",
        timestamp_ns: Date.now() * 1_000_000,
        trace_id: "hud-trace-id",
        payload,
      }),
    });
  }
}

function Socket() {
  useWebSocket();
  return null;
}

function Tail() {
  const view = useHudVoiceTail();
  return <HudVoiceTail view={view} />;
}

function Harness() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <Socket />
      <Tail />
    </QueryClientProvider>
  );
}

const EMPTY_TIMELINE_ITEMS: TimelineItem[] = [];

const send = (name: string, payload: Record<string, unknown>) =>
  act(() => MockWebSocket.last!.deliver(name, payload));

describe("HudVoiceTail — partial → final → streaming reply", () => {
  const OriginalWS = globalThis.WebSocket;

  beforeEach(() => {
    (globalThis as unknown as { WebSocket: typeof MockWebSocket }).WebSocket = MockWebSocket;
    (window as unknown as { location: unknown }).location = { protocol: "http:", host: "localhost:5173" };
    useI18nStore.getState().setUi("en", { push: false });
    useEventStore.setState({ assistantName: "Nova", voiceState: "idle", liveReply: null });
    useEventStore.getState().setTranscription("", true);
    useHomeStore.getState().resetTranscript();
    useAgentChatStore.setState((s) => ({ timeline: { ...s.timeline, items: EMPTY_TIMELINE_ITEMS } }));
  });

  afterEach(() => {
    cleanup();
    (globalThis as unknown as { WebSocket: typeof WebSocket }).WebSocket = OriginalWS;
    MockWebSocket.last = null;
  });

  it("shows words while spoken, settles them, then streams and lands the reply", async () => {
    render(<Harness />);
    await act(async () => {
      await Promise.resolve();
    });
    // Nothing in flight: no tail at all (the chat above has its own empty state).
    expect(screen.queryByTestId("hud-transcript")).toBeNull();

    // 1. The recogniser's partials — the speech pipeline's live probe.
    send("SystemStateChanged", { new_state: "LISTENING", previous: "IDLE" });
    send("TranscriptionUpdate", { text: "what is on", is_final: false });
    expect(screen.getByTestId("hud-transcript-partial").textContent).toContain("what is on");
    send("TranscriptionUpdate", { text: "what is on my calendar", is_final: false });
    const partial = screen.getByTestId("hud-transcript-partial");
    expect(partial.textContent).toContain("what is on my calendar");
    expect(partial.getAttribute("data-live")).toBe("true");
    expect(screen.queryByTestId("hud-transcript-final")).toBeNull();

    // 2. The final transcript replaces the partial with a settled line.
    send("TranscriptFinal", { transcript: { text: "What is on my calendar today?" } });
    send("TranscriptionUpdate", { text: "What is on my calendar today?", is_final: true });
    expect(screen.queryByTestId("hud-transcript-partial")).toBeNull();
    const finals = screen.getAllByTestId("hud-transcript-final");
    expect(finals).toHaveLength(1);
    expect(finals[0].textContent).toContain("What is on my calendar today?");

    // 3. The reply streams as cumulative snapshots …
    send("SystemStateChanged", { new_state: "THINKING", previous: "LISTENING" });
    send("AssistantTextDelta", { text: "You have", channel: "voice", done: false });
    expect(screen.getByTestId("hud-transcript-streaming").textContent).toContain("You have");
    send("AssistantTextDelta", { text: "You have two meetings", channel: "voice", done: false });
    expect(screen.getByTestId("hud-transcript-streaming").textContent).toContain("You have two meetings");

    // 4. … and the spoken line replaces the preview.
    send("SpeechSpoken", { text: "You have two meetings today.", spoken_kind: "reply" });
    expect(screen.queryByTestId("hud-transcript-streaming")).toBeNull();
    const replies = screen.getAllByTestId("hud-transcript-reply");
    expect(replies).toHaveLength(1);
    expect(replies[0].textContent).toContain("You have two meetings today.");
    expect(replies[0].textContent).toContain("NOVA");
    // The order on screen is the order of the turn.
    const all = screen.getByTestId("hud-transcript").querySelectorAll(
      '[data-testid="hud-transcript-final"],[data-testid="hud-transcript-reply"]',
    );
    expect(Array.from(all).map((n) => n.getAttribute("data-testid"))).toEqual([
      "hud-transcript-final",
      "hud-transcript-reply",
    ]);
  });

  it("streams a typed turn's reply too (a question asked from the memory panel)", async () => {
    render(<Harness />);
    await act(async () => {
      await Promise.resolve();
    });
    send("MessageSent", { role: "user", text: "Brief me on Atlas", thread_id: "t1" });
    // What `sendChatMessage` arms: the typed turn is waiting for its reply.
    act(() => useEventStore.getState().setChatThinking(true));
    send("AssistantTextDelta", { text: "Atlas is", channel: "chat", thread_id: "t1", done: false });
    expect(screen.getByTestId("hud-transcript-streaming").textContent).toContain("Atlas is");
    send("MessageSent", { role: "assistant", text: "Atlas is on track.", thread_id: "t1" });
    expect(screen.getAllByTestId("hud-transcript-reply")[0].textContent).toContain("Atlas is on track.");
    expect(screen.queryByTestId("hud-transcript-streaming")).toBeNull();
  });

  it("leaves the tail once the backend filed the turn into the chat", async () => {
    render(<Harness />);
    await act(async () => {
      await Promise.resolve();
    });
    send("TranscriptFinal", { transcript: { text: "Open the garage" } });
    send("SpeechSpoken", { text: "The garage is open.", spoken_kind: "reply" });
    expect(screen.getByTestId("hud-transcript-final").textContent).toContain("Open the garage");
    expect(screen.getByTestId("hud-transcript-reply").textContent).toContain("The garage is open.");

    // `VoiceTurnCompleted` → voice_mirror → the chat's own stream: the turn
    // is in the thread now, so the tail must not show it a second time.
    const at = Date.now() + 5;
    act(() =>
      useAgentChatStore.setState((s) => ({
        timeline: {
          ...s.timeline,
          items: [
            { type: "user", id: "u1", text: "Open the garage", tsMs: at } as TimelineItem,
            {
              type: "turn",
              id: "t1",
              startedMs: at,
              blocks: [{ kind: "text", text: "The garage is open." }],
            } as unknown as TimelineItem,
          ],
        },
      })),
    );
    expect(screen.queryByTestId("hud-transcript")).toBeNull();
  });
});

describe("hudTranscriptView", () => {
  const base = { transcript: [], transcription: "", transcriptionFinal: true, voiceReply: "", chatReply: null };

  it("hides a final partial and a done chat snapshot", () => {
    expect(hudTranscriptView({ ...base, transcription: "hi", transcriptionFinal: true }).partial).toBe("");
    expect(hudTranscriptView({ ...base, chatReply: { text: "x", done: true } }).streaming).toBe("");
  });

  it("drops a late snapshot the settled answer already contains", () => {
    const view = hudTranscriptView({
      ...base,
      transcript: [{ id: "a", ts: 1, who: "assistant", text: "Done. All set." }],
      voiceReply: "Done.",
    });
    expect(view.streaming).toBe("");
  });

  it("keeps only the newest lines on screen while a call is up", () => {
    const transcript = Array.from({ length: 60 }, (_, i) => ({
      id: String(i),
      ts: i + 1,
      who: "user" as const,
      text: `line ${i}`,
    }));
    const view = hudTranscriptView({ ...base, transcript, callLive: true });
    expect(view.lines).toHaveLength(40);
    expect(view.lines[0].text).toBe("line 20");
  });

  it("shows only what the chat does not hold yet", () => {
    const chatItems = [
      { type: "user", id: "u1", text: "yes", tsMs: 100 },
      { type: "turn", id: "t1", startedMs: 110, blocks: [{ kind: "text", text: "Done." }] },
    ] as unknown as TimelineItem[];
    const view = hudTranscriptView({
      ...base,
      chatItems,
      now: 70_000,
      transcript: [
        { id: "a", ts: 90, who: "user", text: "yes" },
        // Stamped a moment after its filed copy: still the same line.
        { id: "b", ts: 900, who: "assistant", text: "Done." },
        // Said again a minute later: a new line, shown although the same
        // word is in the chat.
        { id: "c", ts: 60_000, who: "user", text: "yes" },
      ],
    });
    expect(view.lines.map((l) => l.id)).toEqual(["c"]);
  });

  it("lets a line the chat never received go after a while once the call is over", () => {
    const transcript = [{ id: "a", ts: 1_000, who: "user" as const, text: "hello" }];
    expect(hudTranscriptView({ ...base, transcript, now: 2_000 }).lines).toHaveLength(1);
    expect(hudTranscriptView({ ...base, transcript, now: 1_000 + 10 * 60_000 }).lines).toHaveLength(0);
    expect(
      hudTranscriptView({ ...base, transcript, now: 1_000 + 10 * 60_000, callLive: true }).lines,
    ).toHaveLength(1);
  });
});
