import { useMemo, useState, type FormEvent } from "react";
import { ArrowUp, Loader2, MessagesSquare, Mic, MicOff, PhoneOff, Volume2, VolumeX } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { VoiceWaveform, type WaveformPhase } from "@/components/overlay/VoiceWaveform";
import { voiceInputLevelRef } from "@/lib/voiceInputLevel";
import { HudChatPanel } from "@/components/deck/HudChatPanel";
import { useHudVoiceTail } from "@/components/deck/HudTranscriptPanel";
import { useVoiceToggles } from "@/components/deck/HudControls";
import { transcriptFromTimeline } from "@/components/home/assistantStatus";
import { useHudTalk } from "@/components/stark/useHudTalk";
import { useAgentChatStore } from "@/store/agentChat";
import { useEventStore, type VoiceState } from "@/store/events";
import { useWakeWord } from "@/hooks/useWakeWord";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The command bar under the reactor: one place to talk or type.
 *
 *  - The round button starts or ends a voice call (`useHudTalk`); nothing
 *    listens before it is pressed.
 *  - The field sends a typed message into the HUD's chat (the front page's
 *    agent-chat store, the same thread the chat panel shows).
 *  - The newest answer shows above the bar; the chat button opens the whole
 *    thread, with its history and sessions, over the screen.
 *  - The two small toggles mute the microphone and the assistant's voice.
 *
 * The chat thread stays mounted while it is closed, so a call that ends
 * still opens the chat it was filed into (`HudChatPanel`).
 */
export function CommandBar({ onTalk, className }: { onTalk?: () => void; className?: string }) {
  const t = useT();
  const talk = useHudTalk(onTalk);
  const toggles = useVoiceToggles();
  const send = useAgentChatStore((s) => s.send);
  const chatBusy = useAgentChatStore((s) => s.busy);
  const items = useAgentChatStore((s) => s.timeline.items);
  const tail = useHudVoiceTail();
  const { config: wakeConfig } = useWakeWord();
  const assistantName = useEventStore((s) => s.assistantName);
  const wakePhrase = wakeConfig?.phrase.trim() || assistantName;
  const [text, setText] = useState("");
  const [sendState, setSendState] = useState<"idle" | "sending" | "failed">("idle");
  const [chatOpen, setChatOpen] = useState(false);

  const lastReply = useMemo(() => {
    const lines = transcriptFromTimeline(items);
    for (let i = lines.length - 1; i >= 0; i -= 1) {
      const line = lines[i];
      if (line.who === "assistant" && "text" in line && line.text.trim()) return line.text.trim();
    }
    return "";
  }, [items]);
  const heard = tail.partial;
  const reply = tail.streaming || lastReply;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const message = text.trim();
    if (!message || sendState === "sending") return;
    setSendState("sending");
    try {
      const result = await send(message);
      if (result === "failed") {
        setSendState("failed");
        return;
      }
      setText("");
      setSendState("idle");
    } catch (error) {
      console.warn("HUD message failed to send.", error);
      setSendState("failed");
    }
  };

  const voiceState = talk.voiceState;
  const stateLine = !talk.connected
    ? t("stark.listen.offline")
    : talk.connecting
      ? t("hud2.controls.connecting")
      : voiceState === "listening"
        ? t("stark.listen.listening")
        : voiceState === "thinking" || chatBusy
          ? t("stark.listen.thinking")
          : voiceState === "speaking"
            ? t("stark.listen.speaking")
            : toggles.micMuted
              ? t("stark.bar.asleep")
              : t("stark.listen.idle");
  const talkLabel = talk.active
    ? t("hud2.controls.end_hint")
    : fill(t("hud2.controls.talk_hint"), { wake: wakePhrase });
  const TalkIcon = talk.connecting ? Loader2 : talk.active ? PhoneOff : Mic;

  return (
    <div className={cn("relative flex w-full flex-col items-center gap-2", className)} data-testid="stark-command-bar">
      {/* The whole chat, over the screen; mounted always, shown on demand. */}
      <div
        className={cn(
          "absolute bottom-full left-1/2 z-20 mb-2 h-[min(34rem,62vh)] w-[min(44rem,92vw)] -translate-x-1/2 font-sans",
          !chatOpen && "hidden",
        )}
        data-testid="stark-chat-drawer"
        aria-hidden={!chatOpen}
      >
        <HudChatPanel className="stark-panel h-full" />
      </div>

      {(heard || reply) && !chatOpen && (
        <button
          type="button"
          onClick={() => setChatOpen(true)}
          className="stark-caption w-full max-w-2xl px-4 py-1.5 text-left"
          data-testid="stark-reply"
          title={t("stark.bar.open_chat")}
        >
          {heard && (
            <span className="block truncate text-sm text-[hsl(var(--stark-hot))]">
              {t("stark.bar.you")}: {heard}
            </span>
          )}
          {reply && (
            <span className="line-clamp-2 text-sm leading-snug text-foreground/90">
              <span className="mr-1.5 font-semibold uppercase tracking-[0.18em] text-primary">{wordmarkShort(assistantName)}</span>
              {reply}
            </span>
          )}
        </button>
      )}

      <form
        onSubmit={(e) => void submit(e)}
        className="stark-pill relative flex w-full max-w-3xl items-center gap-3 rounded-full py-2 pl-2 pr-3 sm:gap-4"
        data-voice={voiceState}
      >
        <button
          type="button"
          onClick={talk.press}
          disabled={talk.disabled && !talk.active}
          aria-label={talkLabel}
          title={talkLabel}
          aria-pressed={talk.active}
          data-testid="stark-talk"
          data-active={talk.active ? "true" : "false"}
          className={cn(
            "stark-mic relative flex h-16 w-16 shrink-0 items-center justify-center rounded-full sm:h-[4.5rem] sm:w-[4.5rem]",
            talk.active && "is-live",
            "disabled:cursor-not-allowed disabled:opacity-50",
          )}
        >
          <TalkIcon className={cn("h-7 w-7", talk.connecting && "animate-spin motion-reduce:animate-none")} aria-hidden />
        </button>

        <div className="flex min-w-0 flex-1 flex-col items-center">
          <span
            className="max-w-full truncate whitespace-nowrap text-xs font-semibold uppercase tracking-[0.24em] text-foreground/90 xl:tracking-[0.42em]"
            data-testid="stark-bar-state"
            aria-live="polite"
          >
            {stateLine}
          </span>
          <VoiceWaveform
            levelRef={voiceInputLevelRef}
            phase={waveformPhase(voiceState, talk.connected)}
            count={56}
            className="h-8 w-full"
          />
          <label htmlFor="stark-ask" className="sr-only">
            {t("stark.bar.ask")}
          </label>
          <input
            id="stark-ask"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              if (sendState === "failed") setSendState("idle");
            }}
            placeholder={t("stark.bar.ask")}
            autoComplete="off"
            data-jarvis-chat-input=""
            data-testid="stark-ask"
            className="w-full bg-transparent text-center text-base text-foreground placeholder:text-muted-foreground focus:outline-none"
          />
          {sendState === "failed" && (
            <span role="alert" className="text-[11px] text-[hsl(var(--stark-hot))]" data-testid="stark-send-failed">
              {t("stark.bar.send_failed")}
            </span>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <PillButton
            icon={toggles.micMuted ? MicOff : Mic}
            label={t(toggles.micMuted ? "hud2.controls.mic_unmute" : "hud2.controls.mic_mute")}
            pressed={toggles.micMuted === true}
            disabled={toggles.micBusy || toggles.micMuted === null}
            onClick={() => void toggles.pressMic()}
            testId="stark-mic-mute"
          />
          <PillButton
            icon={toggles.voiceMuted ? VolumeX : Volume2}
            label={t(toggles.voiceMuted ? "hud2.controls.voice_unmute" : "hud2.controls.voice_mute")}
            pressed={toggles.voiceMuted}
            disabled={toggles.voiceBusy}
            onClick={() => void toggles.pressVoice()}
            testId="stark-voice-mute"
          />
          <PillButton
            icon={MessagesSquare}
            label={t(chatOpen ? "stark.bar.close_chat" : "stark.bar.open_chat")}
            pressed={chatOpen}
            onClick={() => setChatOpen((v) => !v)}
            testId="stark-chat-toggle"
          />
          <button
            type="submit"
            disabled={!text.trim() || sendState === "sending"}
            aria-label={t("stark.bar.send")}
            title={t("stark.bar.send")}
            data-testid="stark-send"
            className="ml-1 flex h-9 w-9 items-center justify-center rounded-full border border-primary/60 text-primary transition-colors hover:bg-primary/15 disabled:opacity-30"
          >
            {sendState === "sending" ? (
              <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />
            ) : (
              <ArrowUp className="h-4 w-4" aria-hidden />
            )}
          </button>
        </div>
      </form>
    </div>
  );
}

function PillButton({
  icon: Icon,
  label,
  pressed,
  disabled,
  onClick,
  testId,
}: {
  icon: LucideIcon;
  label: string;
  pressed: boolean;
  disabled?: boolean;
  onClick: () => void;
  testId: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={pressed}
      aria-label={label}
      title={label}
      data-testid={testId}
      className={cn(
        "flex h-9 w-9 items-center justify-center rounded-full transition-colors disabled:opacity-30",
        pressed
          ? "bg-[hsl(var(--stark-hot)/0.15)] text-[hsl(var(--stark-hot))]"
          : "text-muted-foreground hover:bg-primary/10 hover:text-primary",
      )}
    >
      <Icon className="h-4 w-4" aria-hidden />
    </button>
  );
}

/** "J.A.R.V.I.S." reads as JARVIS in a caption. */
function wordmarkShort(name: string): string {
  return (name.trim() || "Jarvis").toUpperCase();
}

export function waveformPhase(state: VoiceState, connected: boolean): WaveformPhase {
  if (!connected) return "idle";
  switch (state) {
    case "connecting":
      return "connecting";
    case "listening":
      return "listening";
    case "thinking":
      return "working";
    case "speaking":
      return "speaking";
    case "error":
      return "error";
    default:
      return "idle";
  }
}
