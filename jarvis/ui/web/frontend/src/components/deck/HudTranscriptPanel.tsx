import { useLayoutEffect, useMemo, useState, type FormEvent } from "react";
import { AudioLines, SendHorizontal } from "lucide-react";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { DeckCard } from "@/components/deck/DeckCard";
import { useStickToBottom } from "@/hooks/useStickToBottom";
import { sendChatMessage } from "@/lib/chat";
import type { TranscriptLine } from "@/lib/homeTranscript";
import { cn } from "@/lib/utils";
import { fill, useT } from "@/i18n";

/** How many settled lines the HUD keeps on screen; the Chats surface has the rest. */
export const HUD_TRANSCRIPT_LINES = 40;

export interface HudTranscriptView {
  /** Settled lines, oldest first: heard words, answers, and step counts. */
  lines: TranscriptLine[];
  /** What the recogniser hears right now, before it settles. "" when nothing. */
  partial: string;
  /** The answer while it is still being produced. "" when nothing streams. */
  streaming: string;
}

/**
 * What the HUD's speech panel shows, from the stores the WebSocket already
 * feeds — no request of its own:
 *
 *  - the PARTIAL: `TranscriptionUpdate` with `is_final: false`, which the
 *    speech pipeline publishes while the person is still talking (its live
 *    probe, the push-to-talk probe, and the realtime session's input
 *    transcript) — the event store's `transcription` while it is not final;
 *  - the FINAL: `TranscriptFinal` / a final `TranscriptionUpdate`, folded into
 *    the home lane (`lib/homeTranscript.ts`) as a user line;
 *  - the REPLY while it STREAMS: `AssistantTextDelta` snapshots — the voice
 *    paths through the home store's `liveReply`, a typed turn (a question
 *    asked from the memory panel) through the event store's `liveReply` —
 *    until the authoritative `SpeechSpoken` / `MessageSent` lands as a line.
 *
 * Pure so the sequence can be pinned without rendering.
 */
export function hudTranscriptView(input: {
  transcript: TranscriptLine[];
  transcription: string;
  transcriptionFinal: boolean;
  voiceReply: string;
  chatReply: { text: string; done: boolean } | null;
}): HudTranscriptView {
  const settled = input.transcript.filter((l) => l.who !== "steps" || l.steps.length > 0);
  const lines = settled.slice(-HUD_TRANSCRIPT_LINES);
  const last = lines[lines.length - 1];
  let partial = input.transcription && !input.transcriptionFinal ? input.transcription.trim() : "";
  // The settled line already says it: a late partial must not echo it.
  if (partial && last?.who === "user" && last.text === partial) partial = "";
  const chat = input.chatReply && !input.chatReply.done ? input.chatReply.text : "";
  let streaming = (input.voiceReply || chat).trim();
  // Hidden once the lane's last line IS the answer (the final arrived and a
  // late snapshot would only repeat it) — the same rule as the voice stage.
  if (streaming && last?.who === "assistant" && last.text.startsWith(streaming)) streaming = "";
  return { lines, partial, streaming };
}

/**
 * The HUD's right-hand panel: what is being said, live.
 *
 * Words appear while they are spoken (amber, with a caret), settle into the
 * heard line, and the answer streams in under them (accent, with a caret)
 * until its final line replaces the preview. Steps of a turn collapse to one
 * telemetry row — the Log card has the detail. The panel follows the newest
 * line unless the reader has scrolled up (`useStickToBottom`).
 *
 * The box at the bottom types into the same conversation the voice uses
 * (`sendChatMessage`, the front page's own send path), so talking, typing and
 * reading all happen here instead of on three pages.
 */
export function HudTranscriptPanel({
  className,
  wakePhrase,
}: {
  className?: string;
  wakePhrase: string;
}) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const voiceState = useEventStore((s) => s.voiceState);
  const transcription = useEventStore((s) => s.transcription);
  const transcriptionFinal = useEventStore((s) => s.transcriptionFinal);
  const chatReply = useEventStore((s) => s.liveReply);
  const transcript = useHomeStore((s) => s.transcript);
  const voiceReply = useHomeStore((s) => s.liveReply);

  const view = useMemo(
    () =>
      hudTranscriptView({ transcript, transcription, transcriptionFinal, voiceReply, chatReply }),
    [transcript, transcription, transcriptionFinal, voiceReply, chatReply],
  );
  const { rootRef, contentRef, atEnd, jumpToEnd, follow } = useStickToBottom();
  useLayoutEffect(follow, [follow, view]);

  const live = Boolean(view.partial || view.streaming) || voiceState === "listening";
  const empty = view.lines.length === 0 && !view.partial && !view.streaming;
  const name = assistantName.toUpperCase();

  return (
    <DeckCard
      icon={AudioLines}
      title={t("hud2.transcript.title")}
      meta={view.partial ? t("hud2.transcript.hearing") : view.streaming ? t("hud2.transcript.replying") : undefined}
      live={live}
      variant="rail"
      className={className}
      bodyClassName="flex min-h-0 flex-col p-0"
    >
      <div data-testid="hud-transcript" className="relative flex min-h-0 flex-1 flex-col">
        <div ref={rootRef} className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
          <div ref={contentRef} className="flex flex-col gap-3 pt-1" aria-live="polite">
            {empty && (
              <p data-testid="hud-transcript-empty" className="pt-2 text-xs leading-relaxed text-muted-foreground">
                {voiceState === "listening"
                  ? t("hud2.transcript.empty_listening")
                  : fill(t("hud2.transcript.empty"), { wake: wakePhrase })}
              </p>
            )}
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
        </div>
        {!atEnd && (
          <button
            type="button"
            onClick={jumpToEnd}
            className="absolute bottom-2 right-3 border border-accent/50 bg-background/80 px-2 py-0.5 font-mono text-micro uppercase tracking-[0.14em] text-accent backdrop-blur-sm transition-colors hover:bg-accent/10"
          >
            {t("hud2.transcript.jump_latest")}
          </button>
        )}
      </div>
      <HudComposer placeholder={fill(t("hud2.transcript.type_placeholder"), { name: assistantName })} sendLabel={t("hud2.transcript.send")} />
    </DeckCard>
  );
}

/** A one-line box that sends a typed turn into the voice conversation. */
function HudComposer({ placeholder, sendLabel }: { placeholder: string; sendLabel: string }) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!draft.trim() || sending) return;
    setSending(true);
    try {
      if (await sendChatMessage(draft)) setDraft("");
    } finally {
      setSending(false);
    }
  };
  return (
    <form
      data-testid="hud-composer"
      onSubmit={submit}
      className="flex shrink-0 items-center gap-2 border-t border-border/60 px-3 py-2"
    >
      <input
        data-testid="hud-composer-input"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="min-w-0 flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
      />
      <button
        type="submit"
        aria-label={sendLabel}
        title={sendLabel}
        disabled={!draft.trim() || sending}
        className="text-accent transition-opacity disabled:opacity-30"
      >
        <SendHorizontal className="h-4 w-4" aria-hidden />
      </button>
    </form>
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
    <div data-testid={testId} data-live={live ? "true" : "false"} className={cn("flex flex-col gap-0.5", user && "pl-0")}>
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
