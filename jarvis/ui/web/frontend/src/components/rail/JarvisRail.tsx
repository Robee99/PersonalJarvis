import { useEffect, useState, type ReactNode } from "react";
import { ArcHalo } from "@/components/stark/ArcHalo";
import { wordmark } from "@/components/stark/StarkShell";
import { HUD_SECTION_IDS } from "@/components/layout/navGroups";
import { clock, useFocusSession } from "@/lib/focusSession";
import { useEventStore, type VoiceState } from "@/store/events";
import { useRailStore, type RailAvatar } from "@/store/rail";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The right-hand rail (the owner's reference, 2026-10-25): the assistant's
 * ring with its state and model, the figure in the ring (RING / CUBE / FACE),
 * and one switch per organ:
 *
 * * EYES — the webcam, read locally for posture (lib/liveVision).
 * * WATCH — Screen Watch: a whole-screen share compared locally every few
 *   seconds; a minute unchanged earns one suggestion.
 * * HOLO — hands steer the brain map (components/orb/hands).
 * * FOCUS — the server-side focus session (jarvis/focus).
 * * REFLEX — the commands answered at once, without a model.
 *
 * On the HUD the big orb already says who is listening, so the rail keeps
 * only its switches there until the brain map opens over it. Off the Stark
 * screens the rail folds to its ring by default, so it never covers a
 * working page. The vision switches' logic lives in VisionControls, which
 * renders this rail.
 */
export interface RailVision {
  webcam: boolean;
  screen: boolean;
  watching: boolean;
  onEyes: () => void;
  onScreen: () => void;
  onWatch: () => void;
  onQuiet: () => void;
  onStopAll: () => void;
  /** Previews, notices and problems from the vision organs, or null. */
  detail: ReactNode;
}

/** Screens drawn in StarkShell, where the brain map (and so HOLO) lives. */
const STARK_SCREENS: readonly string[] = [
  ...HUD_SECTION_IDS,
  "intelligence",
  "orbital",
  "world-intelligence",
  "live-feeds",
  "ghost",
];
const AVATARS: RailAvatar[] = ["ring", "cube", "face"];
const REFLEX_EXAMPLES = ["volume", "mute", "brightness", "window", "website", "focus"] as const;

/** "claude-opus-5-5" → "OPUS 5.5": a hyphen between two digits is a version dot. */
export function modelChip(model: string): string {
  const bare = model.trim().replace(/^(?:claude|models)[-/]/i, "").replace(/^.*\//, "");
  return bare.replace(/(\d)-(?=\d)/g, "$1.").replace(/[-_]+/g, " ").toUpperCase();
}

function stateTone(state: VoiceState): string {
  if (state === "listening") return "bg-success";
  if (state === "thinking" || state === "connecting") return "bg-warning";
  if (state === "speaking") return "bg-primary";
  if (state === "error") return "bg-destructive";
  return "bg-muted-foreground";
}

function Chip({ children, dot }: { children: ReactNode; dot?: string }) {
  return (
    <span className="inline-flex max-w-[9rem] items-center gap-1.5 truncate rounded-full border border-primary/40 bg-background/80 px-2 py-0.5 text-[10px] uppercase tracking-[0.18em] text-primary">
      {dot && <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", dot)} aria-hidden />}
      <span className="truncate">{children}</span>
    </span>
  );
}

function Figure({ avatar, name }: { avatar: RailAvatar; name: string }) {
  if (avatar === "face") {
    return <img src="/jarvis-gigi-256.png" alt="" className="h-14 w-14 rounded-full object-cover" />;
  }
  if (avatar === "cube") {
    return (
      <span className="rail-cube" aria-hidden>
        {["front", "back", "right", "left", "top", "bottom"].map((side) => (
          <span key={side} className={`rail-cube-face rail-cube-${side}`} />
        ))}
      </span>
    );
  }
  return (
    <span className="flex h-16 w-16 items-center justify-center rounded-full border border-primary/60 bg-primary/10 text-[8px] font-semibold tracking-[0.12em] text-primary shadow-[0_0_18px_hsl(var(--primary)/0.45)]">
      {name}
    </span>
  );
}

function Switch({
  label,
  state,
  on,
  onClick,
  testId,
}: {
  label: string;
  state: string;
  on: boolean;
  onClick: () => void;
  testId: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      data-testid={testId}
      className={cn(
        "flex w-full items-center justify-between rounded-md border bg-background/85 px-3 py-1.5 text-[11px] uppercase tracking-[0.2em] backdrop-blur transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong",
        on ? "border-primary/60 text-primary" : "border-border text-foreground hover:bg-secondary",
      )}
    >
      <span>{label}</span>
      <span className={cn("text-[10px] normal-case tracking-normal", on ? "text-primary" : "text-muted-foreground")}>{state}</span>
    </button>
  );
}

function Card({ children, testId }: { children: ReactNode; testId?: string }) {
  return (
    <div data-testid={testId} className="space-y-2 rounded-xl border border-border bg-background/95 p-3 text-xs text-foreground shadow-lg backdrop-blur">
      {children}
    </div>
  );
}

const small = "inline-flex items-center rounded-md border border-border px-2 py-1 text-[11px] hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong";

export function JarvisRail({ vision }: { vision: RailVision }) {
  const t = useT();
  const voiceState = useEventStore((s) => s.voiceState);
  const brainModel = useEventStore((s) => s.brainModel);
  const brainProvider = useEventStore((s) => s.brainProvider);
  const assistantName = useEventStore((s) => s.assistantName);
  const activeSection = useEventStore((s) => s.activeSection);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const { avatar, holo, reflexOpen, folded: foldPick, mapOpen, setAvatar, setHolo, setReflexOpen, setFolded } = useRailStore();
  const focus = useFocusSession();
  const [focusOpen, setFocusOpen] = useState(false);
  const [problem, setProblem] = useState("");
  const onHud = (HUD_SECTION_IDS as readonly string[]).includes(activeSection);
  const f = focus.state;
  // Leaving the Stark screens closes the map and its camera: HOLO goes off too.
  const onStark = STARK_SCREENS.includes(activeSection);
  const folded = !onStark && foldPick;
  const showPresence = !(onHud && !mapOpen);
  useEffect(() => {
    if (holo && !onStark) setHolo(false);
  }, [holo, onStark, setHolo]);

  const act = (work: () => Promise<unknown>) => {
    setProblem("");
    void work().catch((error: unknown) => setProblem(error instanceof Error ? error.message : String(error)));
  };
  const toggleHolo = () => {
    const next = !holo;
    setHolo(next);
    // Hands steer the brain map, which opens from the HUD.
    if (next && !onHud) setActiveSection("chats");
  };
  const toggleFocus = () => {
    if (f?.active) setFocusOpen(!focusOpen);
    else act(async () => { await focus.start(30); setFocusOpen(true); });
  };
  const focusState = f?.active ? clock(f.remaining_s) : t("rail.off");
  const modelText = modelChip(brainModel || brainProvider) || t("rail.no_model");

  return (
    <aside
      aria-label={t("rail.label")}
      data-testid="jarvis-rail"
      className={cn(
        "rail-tone pointer-events-none fixed bottom-3 right-3 z-[90] flex w-44 flex-col items-stretch gap-2 font-mono",
        onStark && "rail-hud",
      )}
    >
      {/* Cards open to the left of the rail, so the rail itself never moves. */}
      <div className="pointer-events-auto absolute bottom-0 right-full mr-3 flex w-72 flex-col gap-2">
        {vision.detail && <Card testId="rail-vision">{vision.detail}
          <div className="flex flex-wrap gap-1">
            <button type="button" className={small} aria-pressed={vision.screen} onClick={vision.onScreen}>{t(vision.screen ? "rail.screen_stop" : "rail.screen_share")}</button>
            <button type="button" className={small} onClick={vision.onQuiet}>{t("rail.quiet")}</button>
            <button type="button" className={small} onClick={vision.onStopAll}>{t("rail.stop_all")}</button>
          </div>
        </Card>}
        {focusOpen && f?.active && (
          <Card testId="rail-focus">
            <p className="font-medium">{f.deferred ? t("rail.focus.deferred") : fill(t("rail.focus.locked"), { target: f.target })}</p>
            {f.goal && <p className="text-muted-foreground">{f.goal}</p>}
            <p className="text-muted-foreground">
              {fill(t("rail.focus.stats"), { left: clock(f.remaining_s), drifts: f.drifts, pct: f.on_target_percent })}
            </p>
            <div className="flex flex-wrap gap-1">
              <button type="button" className={small} onClick={() => act(focus.lock)}>{t("rail.focus.lock")}</button>
              <button type="button" className={small} onClick={() => act(async () => { await focus.stop(); setFocusOpen(false); })}>{t("rail.focus.stop")}</button>
            </div>
            <p className="text-[11px] text-muted-foreground">{t("rail.focus.voice")}</p>
          </Card>
        )}
        {reflexOpen && (
          <Card testId="rail-reflex">
            <p className="font-medium">{t("rail.reflex.title")}</p>
            <ul className="space-y-0.5 text-muted-foreground">
              {REFLEX_EXAMPLES.map((key) => <li key={key}>“{t(`rail.reflex.${key}`)}”</li>)}
            </ul>
          </Card>
        )}
        {problem && <Card><p role="alert">{problem}</p></Card>}
      </div>

      {showPresence && (
        <div className="pointer-events-auto flex flex-col items-center gap-1.5">
          <button
            type="button"
            onClick={() => !onStark && setFolded(!folded)}
            aria-expanded={!folded}
            aria-label={t(folded ? "rail.unfold" : "rail.fold")}
            className="rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong"
          >
            <ArcHalo size={folded ? 64 : 128} voiceState={voiceState} busy={false} context={null} title={wordmark(assistantName)}>
              {folded ? <span className="h-5 w-5 rounded-full bg-primary/30" aria-hidden /> : <Figure avatar={avatar} name={wordmark(assistantName)} />}
            </ArcHalo>
          </button>
          {!folded && (
            <>
              <div className="flex flex-wrap justify-center gap-1.5">
                <Chip dot={stateTone(voiceState)}>{t(`rail.state.${voiceState}`)}</Chip>
                <Chip>{modelText}</Chip>
              </div>
              <div role="radiogroup" aria-label={t("rail.figure")} className="flex gap-1">
                {AVATARS.map((key) => (
                  <button
                    key={key}
                    type="button"
                    role="radio"
                    aria-checked={avatar === key}
                    onClick={() => setAvatar(key)}
                    className={cn(
                      "rounded-full border px-2 py-0.5 text-[9px] uppercase tracking-[0.18em] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong",
                      avatar === key ? "border-primary/60 text-primary" : "border-border bg-background/80 text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {t(`rail.avatar.${key}`)}
                  </button>
                ))}
              </div>
              <p className="text-[10px] text-muted-foreground">
                <span className={cn("mr-1 inline-block h-1.5 w-1.5 rounded-full", stateTone(voiceState))} aria-hidden />
                {t(`rail.line.${voiceState}`)}
              </p>
            </>
          )}
        </div>
      )}

      {!folded && (
        <div className="pointer-events-auto flex flex-col gap-1.5">
          <Switch testId="rail-eyes" label={t("rail.eyes")} on={vision.webcam} state={t(vision.webcam ? "rail.on" : "rail.off")} onClick={vision.onEyes} />
          <Switch testId="rail-watch" label={t("rail.watch")} on={vision.watching} state={t(vision.watching ? "rail.on" : "rail.off")} onClick={vision.onWatch} />
          <Switch testId="rail-holo" label={t("rail.holo")} on={holo} state={t(holo ? "rail.on" : "rail.off")} onClick={toggleHolo} />
          <Switch testId="rail-focus-switch" label={t("rail.focus.label")} on={Boolean(f?.active)} state={focusState} onClick={toggleFocus} />
          <Switch testId="rail-reflex-switch" label={t("rail.reflex.label")} on={reflexOpen} state={t(reflexOpen ? "rail.shown" : "rail.hidden")} onClick={() => setReflexOpen(!reflexOpen)} />
        </div>
      )}
    </aside>
  );
}
