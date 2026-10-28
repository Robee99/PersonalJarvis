import { useMemo } from "react";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { useAgentChatStore } from "@/store/agentChat";
import { useVoiceCall } from "@/components/agentic/useVoiceCall";
import { transcriptFromTimeline } from "@/components/home/assistantStatus";
import type { TimelineItem } from "@/components/agentchat/reduce";
import type { TranscriptLine } from "@/lib/homeTranscript";
import { cn } from "@/lib/utils";
import { fill, useT } from "@/i18n";

/** How many not-yet-filed lines the tail keeps on screen at most. */
export const HUD_TRANSCRIPT_LINES = 40;

/**
 * How long a settled voice line that never reached the chat stays in the
 * thread's tail once the call is over. A filed turn replaces it long before;
 * this only bounds a line the backend never filed (no chat service, a
 * mirror that failed), so the tail cannot grow stale forever.
 */
export const HUD_TAIL_WINDOW_MS = 120_000;

export interface HudTranscriptView {
  /** Settled lines not yet in the chat, oldest first: heard words, answers, step counts. */
  lines: TranscriptLine[];
  /** What the recogniser hears right now, before it settles. "" when nothing. */
  partial: string;
  /** The answer while it is still being produced. "" when nothing streams. */
  streaming: string;
}

/** How far apart the lane's line and its filed copy can be stamped. */
const FILED_RACE_MS = 15_000;
/** How many of the chat's newest lines a streaming answer is compared with. */
const FILED_TEXT_WINDOW = 4;

const norm = (text: string) => text.replace(/\s+/g, " ").trim().toLowerCase();

/**
 * The voice turn that is still in flight, as the tail of the chat thread.
 *
 * Since 2026-10-12 the HUD has ONE conversation list: the real chat (the
 * agent-chat timeline the Chat page showed). A spoken turn reaches that list
 * when it is over — the backend files it into the bound chat
 * (`jarvis/agent_chat/voice_mirror.py`). Until then it only exists on the
 * WebSocket lanes, so this shows exactly that part, under the thread:
 *
 *  - the PARTIAL: `TranscriptionUpdate` with `is_final: false` — the event
 *    store's `transcription` while it is not final;
 *  - the SETTLED lines of the current turn: the home lane
 *    (`lib/homeTranscript.ts`) lines newer than the newest chat item that the
 *    chat does not already hold — once the turn is filed they are in the
 *    thread above and drop out of the tail, so nothing shows twice;
 *  - the REPLY while it STREAMS: `AssistantTextDelta` snapshots — the voice
 *    paths through the home store's `liveReply`, a typed turn on the classic
 *    chat path through the event store's `liveReply` — until its line lands.
 *
 * Pure so the sequence can be pinned without rendering.
 */
export function hudTranscriptView(input: {
  transcript: TranscriptLine[];
  transcription: string;
  transcriptionFinal: boolean;
  voiceReply: string;
  chatReply: { text: string; done: boolean } | null;
  /** The chat on stage (its timeline items); empty when none is open. */
  chatItems?: readonly TimelineItem[];
  /** A call is up: its lines stay in the tail whatever their age. */
  callLive?: boolean;
  /** The clock, for `HUD_TAIL_WINDOW_MS`. */
  now?: number;
}): HudTranscriptView {
  const filed = transcriptFromTimeline(input.chatItems ?? []);
  // Text settles the race where a turn is filed a moment BEFORE the lane's
  // own line for it lands (so its time alone does not hide it). Only a filed
  // line from around the same moment counts: an older "yes" in the chat must
  // not swallow the "yes" just said.
  const alreadyFiled = (line: TranscriptLine) => {
    const text = norm(line.text);
    return filed.some(
      (f) => f.who === line.who && f.ts >= line.ts - FILED_RACE_MS && norm(f.text) === text,
    );
  };
  const lastFiledTs = filed.reduce((max, l) => Math.max(max, l.ts), 0);
  const now = input.now ?? Date.now();
  const fresh = (l: TranscriptLine) => input.callLive || now - l.ts < HUD_TAIL_WINDOW_MS;

  const settled = input.transcript.filter((l) => l.who !== "steps" || l.steps.length > 0);
  const pending = settled.filter(
    (l) =>
      l.ts > lastFiledTs &&
      fresh(l) &&
      (l.who === "steps" || !alreadyFiled(l)),
  );
  // A steps row alone, with the words it belonged to already filed, says nothing.
  const lines = (pending.some((l) => l.who !== "steps") ? pending : []).slice(-HUD_TRANSCRIPT_LINES);
  const last = settled[settled.length - 1];
  let partial = input.transcription && !input.transcriptionFinal ? input.transcription.trim() : "";
  // The settled line already says it: a late partial must not echo it.
  if (partial && last?.who === "user" && last.text === partial) partial = "";
  const chat = input.chatReply && !input.chatReply.done ? input.chatReply.text : "";
  let streaming = (input.voiceReply || chat).trim();
  // Hidden once the answer is a line (the final arrived and a late snapshot
  // would only repeat it) — the same rule as the voice stage — or once the
  // chat itself already holds it.
  if (streaming && last?.who === "assistant" && last.text.startsWith(streaming)) streaming = "";
  const newestFiled = filed.slice(-FILED_TEXT_WINDOW);
  if (streaming && newestFiled.some((l) => l.who === "assistant" && norm(l.text).startsWith(norm(streaming)))) {
    streaming = "";
  }
  return { lines, partial, streaming };
}

/** Is there anything for the tail to show? */
export function hasVoiceTail(view: HudTranscriptView): boolean {
  return view.lines.length > 0 || Boolean(view.partial) || Boolean(view.streaming);
}

/** The voice turn in flight, read from the stores the WebSocket already feeds. */
export function useHudVoiceTail(): HudTranscriptView {
  const transcription = useEventStore((s) => s.transcription);
  const transcriptionFinal = useEventStore((s) => s.transcriptionFinal);
  const chatReply = useEventStore((s) => s.liveReply);
  const transcript = useHomeStore((s) => s.transcript);
  const voiceReply = useHomeStore((s) => s.liveReply);
  const chatItems = useAgentChatStore((s) => s.timeline.items);
  const { active, connecting } = useVoiceCall();
  const callLive = active || connecting;
  return useMemo(
    () =>
      hudTranscriptView({
        transcript,
        transcription,
        transcriptionFinal,
        voiceReply,
        chatReply,
        chatItems,
        callLive,
      }),
    [transcript, transcription, transcriptionFinal, voiceReply, chatReply, chatItems, callLive],
  );
}

/**
 * The spoken turn under the chat thread: words appear while they are spoken
 * (amber, with a caret), settle into the heard line, and the answer streams
 * in under them (accent, with a caret) until the turn is filed into the chat
 * above. Steps of a turn collapse to one telemetry row — the Log card has
 * the detail.
 */
export function HudVoiceTail({ view }: { view: HudTranscriptView }) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const name = assistantName.toUpperCase();
  if (!hasVoiceTail(view)) return null;
  return (
    <div data-testid="hud-transcript" className="flex flex-col gap-3" aria-live="polite">
      {view.lines.map((line) =>
        line.who === "steps" ? (
          <div
            key={line.id}
            data-testid="hud-transcript-steps"
            className="flex items-center gap-2 font-mono text-micro uppercase tracking-[0.16em] text-muted-foreground"
          >
            <span className="h-px w-4 bg-border" aria-hidden />
            {fill(t("hud2.transcript.steps"), { n: line.steps.length })}
            {line.durationMs !== undefined && (
              <span className="tabular-nums">· {(line.durationMs / 1000).toFixed(1)}s</span>
            )}
          </div>
        ) : (
          <Utterance
            key={line.id}
            testId={line.who === "user" ? "hud-transcript-final" : "hud-transcript-reply"}
            who={line.who === "user" ? t("hud2.transcript.you").toUpperCase() : name}
            tag={line.who === "user" ? t("hud2.transcript.heard") : t("hud2.transcript.reply")}
            ts={line.ts}
            text={line.text}
            user={line.who === "user"}
          />
        ),
      )}
      {view.partial && (
        <Utterance
          testId="hud-transcript-partial"
          who={t("hud2.transcript.you").toUpperCase()}
          tag={t("hud2.transcript.hearing")}
          text={view.partial}
          user
          live
        />
      )}
      {view.streaming && (
        <Utterance
          testId="hud-transcript-streaming"
          who={name}
          tag={t("hud2.transcript.replying")}
          text={view.streaming}
          user={false}
          live
        />
      )}
    </div>
  );
}

/**
 * One utterance: a telemetry label (who · state · time) over the words.
 * The user's words are amber while still being heard and settle to the
 * foreground; the assistant's answer is accent while it streams.
 */
function Utterance({
  who,
  tag,
  ts,
  text,
  user,
  live = false,
  testId,
}: {
  who: string;
  tag: string;
  ts?: number;
  text: string;
  user: boolean;
  live?: boolean;
  testId: string;
}) {
  return (
    <div data-testid={testId} data-live={live ? "true" : "false"} className="flex flex-col gap-0.5">
      <div
        className={cn(
          "flex items-center gap-1.5 font-mono text-micro uppercase tracking-[0.18em]",
          live ? (user ? "text-warning" : "text-accent") : "text-muted-foreground",
        )}
      >
        <span
          aria-hidden
          className={cn(
            "h-1.5 w-1.5 rounded-full",
            live ? (user ? "bg-warning" : "bg-accent") : user ? "bg-muted-foreground/60" : "bg-accent/60",
            live && "animate-pulse motion-reduce:animate-none",
          )}
        />
        <span>{who}</span>
        <span className="opacity-60">· {tag}</span>
        {ts !== undefined && <span className="ml-auto tabular-nums opacity-60">{clock(ts)}</span>}
      </div>
      <p
        className={cn(
          "whitespace-pre-wrap text-sm leading-relaxed",
          user ? "border-l border-warning/40 pl-2" : "border-l border-accent/40 pl-2",
          live ? (user ? "italic text-foreground/80" : "text-foreground/85") : "text-foreground",
        )}
      >
        {text}
        {live && (
          <span
            aria-hidden
            className={cn(
              "ml-0.5 inline-block h-[1em] w-0.5 translate-y-0.5 animate-pulse motion-reduce:animate-none",
              user ? "bg-warning" : "bg-accent",
            )}
          />
        )}
      </p>
    </div>
  );
}

function clock(ts: number): string {
  const d = new Date(ts);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
