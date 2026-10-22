import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, MotionConfig, motion, useReducedMotion } from "framer-motion";
import { useEventStore, type VoiceState } from "@/store/events";
import { useDeckStore } from "@/store/deck";
import { DeckOrb, type OrbReadouts } from "@/components/deck/DeckOrb";
import type { ThinkingStep } from "@/lib/thinkingSteps";
import { DeckStandby, ORB_TRAVEL } from "@/components/deck/DeckStandby";
import { DeckReveal } from "@/components/deck/DeckReveal";
import { ArcHalo } from "@/components/stark/ArcHalo";
import { useDeckContext } from "@/components/stark/ContextMeter";
import { QuickAccess, ResourcesPanel, SystemStatusPanel } from "@/components/stark/StarkInstruments";
import { TasksPanel } from "@/components/stark/TasksPanel";
import { WeatherMini } from "@/components/stark/WeatherGlobe";
import { WorldDotMap } from "@/components/stark/WorldDotMap";
import { NewsTicker } from "@/components/stark/WorldPanels";
import { CommandBar } from "@/components/stark/CommandBar";
import { StarkShell, wordmark } from "@/components/stark/StarkShell";
import { useHudTalk } from "@/components/stark/useHudTalk";
import { useVoiceEngineDisplay } from "@/hooks/useVoiceEngineDisplay";
import { useAssistantStatus } from "@/hooks/useAssistantStatus";
import { formatAssistantStatus } from "@/lib/assistantStatus";
import { useWakeWord } from "@/hooks/useWakeWord";
import { useElementSize } from "@/hooks/useElementSize";
import { stageVignette, stageWashSize } from "@/lib/deckStage";
import { HANDOFF, autoLaunchAfterMs, resolvePhase } from "@/lib/deckStandby";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

export { wordmark };

/**
 * The HUD's Command screen — the front page.
 *
 * HUD v5 (2026-10-18, the owner's concept boards as the real screen): the
 * Stark frame (`StarkShell`: the rail, the clock, the name and its state,
 * search / new chat / settings) around one stage:
 *
 *   ┌rail┬ 21:42 ─────────── J.A.R.V.I.S. · ONLINE ─────────── ⌕ ✎ ⚙ ┐
 *   │    │ [SYSTEM STATUS]      (   the arc reactor over   ) [RESOURCES] │
 *   │    │ [QUICK ACCESS ]      (   a dotted world map     ) [TASKS    ] │
 *   │    │                  [ ◉  LISTENING… / Ask me anything ] (globe°) │
 *   │    │ ◆ BREAKING  headline · headline · headline …                  │
 *   └────┴────────────────────────────────────────────────────────────────┘
 *
 * The chat is the command bar: talk with the round button, type into the
 * field, read the newest answer above it, open the whole thread (history,
 * sessions, approvals) from its chat button. The memory orb opens from the
 * rail. Narrower than 1024 px the board is one column and scrolls.
 *
 * Nothing on this screen is invented: a number the store or an endpoint
 * cannot source is not shown, and a list that is empty says so.
 *
 * The board is the deck's SECOND act. While the app comes up the stage is
 * `DeckStandby` — the boot sequence — and the board takes over the moment a
 * turn opens or the person asks for it (`lib/deckStandby.ts::resolvePhase`,
 * forward only), or on its own a blink after the gates are up
 * (`lib/deckStandby.ts::AUTO_LAUNCH`). The hand-off is a launch on one clock
 * (`lib/deckStandby.ts::HANDOFF`): the reactor travels from the ring's centre
 * to its place on the board (one `layoutId` on both stages), the panels
 * assemble outward (`DeckReveal`), and one scan runs down the board.
 */
export function MissionDeckView() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const connected = useEventStore((s) => s.connected);
  const voiceReady = useEventStore((s) => s.voiceReady);
  const voiceState = useEventStore((s) => s.voiceState);
  const chatThinking = useEventStore((s) => s.chatThinking);
  const thinkingSteps = useEventStore((s) => s.thinkingSteps);
  const messages = useEventStore((s) => s.messages);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const wordsSession = useDeckStore((s) => s.wordsSession);
  const turnPhase = useDeckStore((s) => s.turn.phase);
  const turnIndex = useDeckStore((s) => s.turn.index);
  const boardOpen = useDeckStore((s) => s.boardOpen);
  const openBoard = useDeckStore((s) => s.openBoard);
  const status = useAssistantStatus();
  // The engine that will answer the next spoken turn, not the dormant sibling.
  const engine = useVoiceEngineDisplay();
  // The reactor and the command bar's round button are the same press.
  const talk = useHudTalk(openBoard);
  const { config: wakeConfig } = useWakeWord();
  const wakePhrase = wakeConfig?.phrase.trim() || assistantName;

  // Which act: the start sequence or the board — forward only for the session.
  const phase = resolvePhase({
    boardOpen,
    turnIndex,
    messageCount: messages.length,
    voiceEngaged: talk.active || talk.connecting,
  });
  // The first board render latches the store's sticky flag, so a transient
  // reason for the board can never bring the start sequence back over it.
  useEffect(() => {
    if (phase === "board") openBoard();
  }, [phase, openBoard]);
  // The start sequence launches itself a blink after the gates are up.
  useEffect(() => {
    if (phase === "board") return;
    const after = autoLaunchAfterMs({ connected, voiceReady });
    if (after === null) return;
    const id = window.setTimeout(() => openBoard(), after);
    return () => window.clearTimeout(id);
  }, [phase, connected, voiceReady, openBoard]);
  // The board powers on with its choreography ONLY when it takes over from
  // the standby on this very screen; fixed on the first render.
  const mountedInto = useRef(phase);
  const revealBoard = mountedInto.current !== "board";
  const running = useMemo(() => thinkingSteps.filter((s) => s.status === "active"), [thinkingSteps]);
  // The deck's own turn counts too: the text chat's thinking flag never arms
  // for a voice turn, and the reactor must sweep for those as well.
  const busy =
    chatThinking ||
    running.length > 0 ||
    voiceState === "thinking" ||
    turnPhase === "think" ||
    turnPhase === "act";
  const statusLabel =
    status.phase === "ready" && busy ? t("voice_state.thinking") : formatAssistantStatus(status, t);

  const orbPressLabel = talk.active ? t("deck.orb_hangup") : t("deck.orb_call").replace("{0}", wakePhrase);
  const readouts: OrbReadouts = {
    nw: statusLabel,
    ne: `${running.length} ${t("deck.orb_steps")}`,
    sw: engine.providerLabel,
    se: `${wordsSession} ${t("deck.orb_words")}`,
  };

  const badge = (
    <div className="flex flex-col items-end gap-0.5 text-right">
      <button
        type="button"
        data-testid="deck-stat-engine"
        onClick={() => setActiveSection("apikeys")}
        className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground hover:text-primary"
      >
        {t(engine.tier === "realtime" ? "deck.stat_realtime" : "deck.stat_brain")}{" "}
        <span className="text-primary">{engine.providerLabel}</span>
      </button>
      <button
        type="button"
        data-testid="deck-stat-model"
        onClick={() => setActiveSection("apikeys")}
        className="max-w-[16rem] truncate text-xs font-medium text-foreground/80 hover:text-primary"
        title={engine.model || undefined}
      >
        {engine.model || "—"}
      </button>
    </div>
  );

  return (
    <MotionConfig reducedMotion="user">
      <StarkShell screen="command" badge={badge} testId="stark-hud">
        <div className="relative flex min-h-0 flex-1 flex-col">
          {phase === "board" && (
            <div
              data-testid="deck-board"
              className={cn(
                "relative grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-y-auto overflow-x-hidden px-4 pb-3 pt-1 xl:px-6",
                "lg:grid-cols-[minmax(250px,21rem)_minmax(0,1fr)_minmax(300px,25rem)] lg:grid-rows-[minmax(0,1fr)_auto] lg:overflow-hidden",
              )}
            >
              {/* The world behind the reactor, faint: the stage's floor. */}
              <WorldDotMap
                dotAlpha={0.28}
                className="pointer-events-none absolute inset-x-0 top-[4%] bottom-[14%] -z-0 opacity-90"
                testId="stark-backdrop"
              />

              {/* LEFT: the status lamps and the quick doors. */}
              <DeckReveal
                slot="left-top"
                reveal={revealBoard}
                className="order-3 lg:order-none lg:col-start-1 lg:row-start-1 lg:min-h-0"
                bodyClassName="flex min-h-0 flex-col justify-center gap-4 lg:h-full lg:overflow-y-auto"
              >
                <SystemStatusPanel brainLabel={engine.providerLabel} modelLabel={engine.model} className="shrink-0" />
                <QuickAccess className="shrink-0" />
              </DeckReveal>

              {/* CENTRE: the reactor in its halo — press it to talk. */}
              <div className="order-1 flex min-h-[26rem] flex-col lg:order-none lg:col-start-2 lg:row-start-1 lg:min-h-0">
                <BoardCentre
                  steps={running}
                  busy={busy}
                  readouts={readouts}
                  onPress={talk.press}
                  pressLabel={orbPressLabel}
                  pressDisabled={talk.disabled}
                  reveal={revealBoard}
                  voiceState={voiceState}
                  title={wordmark(assistantName)}
                />
              </div>

              {/* RIGHT: the machine, then the work. */}
              <DeckReveal
                slot="right-top"
                reveal={revealBoard}
                className="order-4 lg:order-none lg:col-start-3 lg:row-start-1 lg:min-h-0"
                bodyClassName="flex min-h-0 flex-col gap-4 lg:h-full"
              >
                <ResourcesPanel className="shrink-0" />
                <TasksPanel className="min-h-[12rem] lg:min-h-0 lg:flex-1" />
              </DeckReveal>

              {/* BOTTOM: the command bar under the reactor, the weather in the corner. */}
              <DeckReveal
                slot="centre-top"
                reveal={revealBoard}
                className="order-2 lg:order-none lg:col-start-2 lg:row-start-2"
                bodyClassName="flex justify-center"
              >
                <CommandBar onTalk={openBoard} />
              </DeckReveal>
              <div className="order-5 flex items-end justify-end lg:order-none lg:col-start-3 lg:row-start-2">
                <WeatherMini />
              </div>

              {/* The launch's last beat: one scan down the whole board. */}
              {revealBoard && <BoardSweep />}
            </div>
          )}

          {/* Before the board: the start sequence, over the stage so its exit
              plays over the board powering on underneath. */}
          <AnimatePresence>
            {phase !== "board" && (
              <DeckStandby
                key="standby"
                className="absolute inset-0 z-10"
                steps={running}
                busy={busy}
                readouts={readouts}
                wakeConfig={wakeConfig}
                onPressOrb={talk.press}
                pressLabel={orbPressLabel}
                pressDisabled={talk.disabled}
                onOpenBoard={openBoard}
              />
            )}
          </AnimatePresence>
        </div>
        <div className="shrink-0 px-4 pb-3 xl:px-6">
          <NewsTicker />
        </div>
      </StarkShell>
    </MotionConfig>
  );
}

/** The halo's bounds: below the minimum the orb's readouts collide, above
 * the maximum the rings stop reading as an instrument and start reading as
 * wallpaper. The orb sits inside the halo's inner race. */
const HUD_HALO_MIN = 220;
const HUD_HALO_MAX = 640;
const ORB_IN_HALO = 0.7;

/** The halo size for the centre stage — as large as the room allows. */
function hudHaloSize(width: number, height: number): number {
  if (!width || !height) return 460;
  const fit = Math.min(width - 16, height - 8);
  return Math.max(HUD_HALO_MIN, Math.min(HUD_HALO_MAX, Math.floor(fit)));
}

/**
 * The board's centre: the orb on its vignetted stage. Since HUD v2 the
 * headline under it is gone — the transcript panel carries the words and the
 * controls under this stage carry the state — so the orb takes all of the
 * room it is given, up to `HUD_ORB_MAX`. It measures ITSELF
 * because it mounts with the board, not with the view: a measurement taken
 * by the view while the standby is up would find no stage and never look
 * again.
 */
function BoardCentre({
  steps,
  busy,
  readouts,
  onPress,
  pressLabel,
  pressDisabled,
  reveal,
  voiceState,
  title,
}: {
  steps: ThinkingStep[];
  busy: boolean;
  readouts: OrbReadouts;
  onPress: () => void;
  pressLabel: string;
  pressDisabled: boolean;
  reveal: boolean;
  voiceState: VoiceState;
  title: string;
}) {
  const stageRef = useRef<HTMLDivElement>(null);
  const stage = useElementSize(stageRef);
  const haloSize = hudHaloSize(stage.width, stage.height);
  const orbSize = Math.round(haloSize * ORB_IN_HALO);
  const context = useDeckContext().data;
  const ctx =
    context?.available && typeof context.used === "number" && context.window
      ? { used: context.used, window: context.window }
      : null;
  const reduced = useReducedMotion() ?? false;
  // The landing ring fires once and leaves the DOM.
  const [landed, setLanded] = useState(false);
  return (
    <div
      ref={stageRef}
      className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-2 text-center"
    >
      {/* The same layoutId as the standby's orb: when the board takes over,
          the orb travels here instead of blinking — and lands with a ring. */}
      <ArcHalo size={haloSize} voiceState={voiceState} busy={busy} context={ctx} title={title}>
      <div className="relative isolate">
        {/* The wash that lifts the centre off the background. It rides on the
            orb, on a square of its own, because it used to be a background
            on the column above — where the column's straight top and bottom
            cut the circle while it was still opaque (deckStage.ts). */}
        <div
          aria-hidden
          data-testid="deck-stage-wash"
          className="pointer-events-none absolute left-1/2 top-1/2 -z-10 -translate-x-1/2 -translate-y-1/2 rounded-full"
          style={{
            width: stageWashSize(orbSize),
            height: stageWashSize(orbSize),
            backgroundImage: stageVignette(orbSize),
          }}
        />
        {/* The mascot stands on the case's floor: a ground shadow under it. */}
        <div aria-hidden data-testid="deck-orb-ground" className="deck-orb-ground" />
        <motion.div layoutId="deck-orb" layoutDependency={orbSize} transition={ORB_TRAVEL}>
          <DeckOrb
            steps={steps}
            busy={busy}
            size={orbSize}
            readouts={readouts}
            onPress={onPress}
            pressLabel={pressLabel}
            pressDisabled={pressDisabled}
          />
        </motion.div>
        {reveal && !reduced && !landed && (
          <motion.div
            aria-hidden
            data-testid="deck-orb-landing"
            className="deck-handoff-wave pointer-events-none absolute inset-0 rounded-full"
            initial={{ opacity: 0, transform: "scale(0.7)" }}
            animate={{ opacity: [0, 0.85, 0], transform: ["scale(0.7)", "scale(1.05)", "scale(1.35)"] }}
            transition={{ delay: HANDOFF.landDelayS, duration: HANDOFF.landS, times: [0, 0.15, 1], ease: "easeOut" }}
            onAnimationComplete={() => setLanded(true)}
          />
        )}
      </div>
      </ArcHalo>
    </div>
  );
}

/**
 * The launch's last beat: once the instruments are in, one scan line runs
 * down the whole board and is gone — the deck is live. Mounted only for a
 * board that took over from the standby on this screen; unmounts itself.
 */
function BoardSweep() {
  const reduced = useReducedMotion() ?? false;
  const [done, setDone] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  // The board's height, once: the bar travels it as a transform (off the
  // main thread), not as `top`.
  const [travel, setTravel] = useState(0);
  useLayoutEffect(() => {
    const parent = ref.current?.parentElement;
    if (parent) setTravel(parent.clientHeight);
  }, []);
  if (reduced || done) return null;
  return (
    <motion.div
      ref={ref}
      aria-hidden
      data-testid="deck-board-sweep"
      className="deck-scan-bar deck-scan-bar-h deck-board-sweep"
      initial={{ transform: "translateY(0px)", opacity: 0 }}
      animate={{ transform: `translateY(${travel}px)`, opacity: [0, 1, 1, 0] }}
      transition={{
        transform: { delay: HANDOFF.boardSweepDelayS, duration: HANDOFF.boardSweepS, ease: [0.3, 0, 0.2, 1] },
        opacity: { delay: HANDOFF.boardSweepDelayS, duration: HANDOFF.boardSweepS, times: [0, 0.1, 0.8, 1] },
      }}
      onAnimationComplete={() => setDone(true)}
    />
  );
}
