import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Mic, MicOff, PhoneOff, Power, Volume2, VolumeX } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEventStore } from "@/store/events";
import { useDeckStore } from "@/store/deck";
import { fetchTtsVolume, setTtsVolume } from "@/lib/voiceApi";
import { SPEAKER_MUTE_EVENT } from "@/lib/speakerMute";
import type { AssistantStatus } from "@/lib/assistantStatus";
import { cn } from "@/lib/utils";
import { fill, useT } from "@/i18n";

/** Seed the microphone mute once: the pipeline's flag, or null when voice is off. */
async function fetchMicMute(): Promise<boolean | null> {
  try {
    const res = await fetch("/api/voice/mute", { cache: "no-store" });
    if (!res.ok) return null;
    const body = (await res.json()) as { available?: boolean; muted?: boolean };
    return body.available ? Boolean(body.muted) : null;
  } catch {
    // Headless / not served yet: the live VoiceMuteChanged still updates it.
    return null;
  }
}

/** Ask the pipeline to flip the microphone mute; the answer comes back on the bus. */
async function toggleMicMute(): Promise<void> {
  const res = await fetch("/api/voice/mute", { method: "POST", cache: "no-store" });
  if (!res.ok) throw new Error(res.status === 503 ? "unavailable" : `HTTP ${res.status}`);
}

/**
 * The HUD's primary controls, under the orb: the state in one large word,
 * then TALK / END as the one big button, and the two mutes beside it.
 *
 *  - TALK / END is the same start/stop path the orb press and every other
 *    voice surface take (`useVoiceCall`), handed in by the view so the orb
 *    and this button can never disagree.
 *  - MIC mutes the microphone FOR the assistant (input only — it finishes
 *    what it is saying). The pipeline owns the flag: the press posts a
 *    toggle, the button mirrors `VoiceMuteChanged` from the deck store.
 *  - VOICE silences the assistant's voice for this session (TTS volume 0,
 *    never persisted), mirrored from `VoiceSpeakerMuteChanged` — the same
 *    contract as the voice bubble's speaker.
 */
export function HudControls({
  status,
  statusLabel,
  callActive,
  busy,
  connecting,
  disabled,
  onTalk,
  wakePhrase,
  className,
}: {
  status: AssistantStatus;
  statusLabel: string;
  callActive: boolean;
  busy: boolean;
  connecting: boolean;
  disabled: boolean;
  onTalk: () => void;
  wakePhrase: string;
  className?: string;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const micMuted = useDeckStore((s) => s.micMuted);
  const setMicMuted = useDeckStore((s) => s.setMicMuted);
  const [micBusy, setMicBusy] = useState(false);
  const [voiceMuted, setVoiceMuted] = useState(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const prevVolume = useRef(1);

  useEffect(() => {
    let cancelled = false;
    void fetchMicMute().then((muted) => {
      // A live event that arrived first is newer than this seed.
      if (!cancelled && muted !== null && useDeckStore.getState().micMuted === null) setMicMuted(muted);
    });
    fetchTtsVolume()
      .then((state) => {
        if (cancelled) return;
        if (state.volume > 0) prevVolume.current = state.volume;
        setVoiceMuted(state.volume === 0);
      })
      .catch(() => {
        // Unknown volume: treat as audible; the first press simply mutes.
      });
    const onSpeaker = (event: Event) => {
      const muted = (event as CustomEvent<{ muted?: unknown }>).detail?.muted;
      if (typeof muted === "boolean") setVoiceMuted(muted);
    };
    window.addEventListener(SPEAKER_MUTE_EVENT, onSpeaker);
    return () => {
      cancelled = true;
      window.removeEventListener(SPEAKER_MUTE_EVENT, onSpeaker);
    };
  }, [setMicMuted]);

  const pressMic = useCallback(async () => {
    if (micBusy) return;
    setMicBusy(true);
    try {
      await toggleMicMute();
    } catch (error) {
      pushToast(
        "error",
        (error as Error).message === "unavailable" ? t("hud2.controls.unavailable") : (error as Error).message,
      );
    } finally {
      setMicBusy(false);
    }
  }, [micBusy, pushToast, t]);

  const pressVoice = useCallback(async () => {
    if (voiceBusy) return;
    const next = !voiceMuted;
    setVoiceBusy(true);
    try {
      await setTtsVolume(next ? 0 : prevVolume.current || 1, false);
      setVoiceMuted(next);
    } catch (error) {
      pushToast("error", (error as Error).message);
    } finally {
      setVoiceBusy(false);
    }
  }, [pushToast, voiceBusy, voiceMuted]);

  const tone =
    status.tone === "error"
      ? "text-destructive"
      : status.tone === "warn"
        ? "text-warning"
        : status.tone === "busy"
          ? "text-accent"
          : status.tone === "ok"
            ? "text-foreground"
            : "text-muted-foreground";
  const talkLabel = connecting
    ? t("hud2.controls.connecting")
    : callActive
      ? t("hud2.controls.end")
      : t("hud2.controls.talk");
  const talkHint = callActive ? t("hud2.controls.end_hint") : fill(t("hud2.controls.talk_hint"), { wake: wakePhrase });
  const TalkIcon = connecting ? Loader2 : callActive ? PhoneOff : Power;

  return (
    <div
      role="group"
      aria-label={t("hud2.controls.group")}
      data-testid="hud-controls"
      className={cn("flex w-full flex-col items-center gap-3", className)}
    >
      {/* The state, in one word big enough to read across the room. */}
      <div className="flex flex-col items-center gap-0.5" aria-live="polite">
        <span className="font-mono text-micro uppercase tracking-[0.3em] text-muted-foreground">
          {t("hud2.controls.state")}
        </span>
        <span
          data-testid="hud-state"
          data-phase={status.phase}
          className={cn("font-mono text-lg uppercase tracking-[0.24em] sm:text-xl", tone)}
        >
          {statusLabel}
        </span>
      </div>

      <div className="flex items-center justify-center gap-3">
        <ToggleButton
          testId="hud-mic"
          icon={micMuted ? MicOff : Mic}
          caption={micMuted ? t("hud2.controls.mic_muted") : t("hud2.controls.mic")}
          label={micMuted ? t("hud2.controls.mic_unmute") : t("hud2.controls.mic_mute")}
          pressed={micMuted === true}
          disabled={micBusy || micMuted === null}
          onClick={() => void pressMic()}
        />
        <button
          type="button"
          data-testid="hud-talk"
          data-active={callActive ? "true" : "false"}
          onClick={onTalk}
          disabled={disabled || busy || connecting}
          title={talkHint}
          aria-label={talkHint}
          className={cn(
            "group relative flex h-14 min-w-[9.5rem] items-center justify-center gap-2 rounded-full border px-6",
            "font-mono text-sm uppercase tracking-[0.28em] transition-[background-color,border-color,box-shadow,color] duration-200",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/70 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
            "disabled:cursor-not-allowed disabled:opacity-50",
            callActive
              ? "border-destructive/70 bg-destructive/10 text-destructive hover:bg-destructive/20"
              : "border-accent/70 bg-accent/10 text-accent hover:bg-accent/20 hover:shadow-[0_0_24px_-4px_hsl(var(--accent)/0.6)]",
          )}
        >
          {/* A thin outer ring — the HUD's one decorative line on the control. */}
          <span
            aria-hidden
            className={cn(
              "pointer-events-none absolute -inset-1.5 rounded-full border",
              callActive ? "border-destructive/25" : "border-accent/25",
            )}
          />
          <TalkIcon className={cn("h-4 w-4", connecting && "animate-spin motion-reduce:animate-none")} />
          <span>{talkLabel}</span>
        </button>
        <ToggleButton
          testId="hud-voice"
          icon={voiceMuted ? VolumeX : Volume2}
          caption={voiceMuted ? t("hud2.controls.voice_muted") : t("hud2.controls.voice")}
          label={voiceMuted ? t("hud2.controls.voice_unmute") : t("hud2.controls.voice_mute")}
          pressed={voiceMuted}
          disabled={voiceBusy}
          onClick={() => void pressVoice()}
        />
      </div>

      {!callActive && (
        <p className="font-mono text-micro uppercase tracking-[0.16em] text-muted-foreground">
          {fill(t("hud2.controls.wake_hint"), { wake: wakePhrase })}
        </p>
      )}
    </div>
  );
}

function ToggleButton({
  icon: Icon,
  caption,
  label,
  pressed,
  disabled,
  onClick,
  testId,
}: {
  icon: LucideIcon;
  caption: string;
  label: string;
  pressed: boolean;
  disabled?: boolean;
  onClick: () => void;
  testId: string;
}) {
  return (
    <div className="flex w-16 flex-col items-center gap-1">
      <button
        type="button"
        data-testid={testId}
        aria-pressed={pressed}
        aria-label={label}
        title={label}
        disabled={disabled}
        onClick={onClick}
        className={cn(
          "flex h-11 w-11 items-center justify-center rounded-full border transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/70",
          "disabled:cursor-not-allowed disabled:opacity-40",
          pressed
            ? "border-warning/70 bg-warning/10 text-warning hover:bg-warning/20"
            : "border-border text-muted-foreground hover:border-accent/60 hover:text-accent",
        )}
      >
        <Icon className="h-4 w-4" />
      </button>
      <span
        className={cn(
          "truncate font-mono text-[10px] uppercase tracking-[0.14em]",
          pressed ? "text-warning" : "text-muted-foreground",
        )}
      >
        {caption}
      </span>
    </div>
  );
}
