import {
  lazy,
  Suspense,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { AnimatePresence, MotionConfig, motion, useReducedMotion } from "framer-motion";
import { useEventStore, type VoiceState } from "@/store/events";
import { useDeckStore } from "@/store/deck";
import { VoiceWaveform, type WaveformPhase } from "@/components/overlay/VoiceWaveform";
import { voiceInputLevelRef } from "@/lib/voiceInputLevel";
import { DockRail } from "@/components/layout/DockRail";

import { PetMark } from "@/components/pets/PetMark";
import { DeckOrb, type OrbReadouts } from "@/components/deck/DeckOrb";
import type { ThinkingStep } from "@/lib/thinkingSteps";
import { DeckStandby, ORB_TRAVEL } from "@/components/deck/DeckStandby";
import { DeckReveal } from "@/components/deck/DeckReveal";
import { DeckFloor, useDeckParallax } from "@/components/deck/DeckStage3D";
import { SLOT_DEPTH, slotDepthVars } from "@/lib/deckDepth";
import { HudLamp } from "@/components/deck/HudFrame";
import { OutputsCard, RunsCard } from "@/components/deck/DeckActivityCards";
import { ApiStatsCard, LiveCounter, VitalsCard } from "@/components/deck/DeckSignalCards";
import { LogCard } from "@/components/deck/DeckLogCard";
import { TurnCard } from "@/components/deck/DeckTurnCard";
import { HudControls } from "@/components/deck/HudControls";
import { HudMemoryPanel, type FullMapRequest } from "@/components/deck/HudMemoryPanel";
import { HudChatPanel } from "@/components/deck/HudChatPanel";
import { bindVoiceChat } from "@/lib/agentChatApi";
import { useAgentChatStore } from "@/store/agentChat";
import { MissionProgressCard } from "@/components/deck/MissionProgressCard";
import { useVoiceEngineDisplay } from "@/hooks/useVoiceEngineDisplay";
import { useAssistantStatus } from "@/hooks/useAssistantStatus";
import { formatAssistantStatus } from "@/lib/assistantStatus";
import { useWakeWord } from "@/hooks/useWakeWord";
import { useVoiceCall } from "@/components/agentic/useVoiceCall";
import { useElementSize } from "@/hooks/useElementSize";
import { stageVignette, stageWashSize } from "@/lib/deckStage";
import { HANDOFF, autoLaunchAfterMs, resolvePhase, type BoardSlot } from "@/lib/deckStandby";
import { writeDeckMode } from "@/lib/deckMode";
import { useDeckAvatar, writeDeckAvatar } from "@/lib/deckAvatar";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { X } from "lucide-react";

/**
 * The full memory map (the 2D maps, hand control, the data import) — opened
 * over the HUD from the memory panel. Its own chunk: force-graph and the
 * hand tracker stay off the front page until someone asks for them.
 */
const MemoryOrbView = lazy(() =>
  import("@/views/MemoryOrbView").then((m) => ({ default: m.MemoryOrbView })),
);

/**
 * The mission deck — the front page.
 *
 * One stage that shows everything the assistant is and does, without a
 * single navigation step.
 *
 * HUD v3 (2026-10-12, owner: "the HUD and the chat are still different …
 * they are not all together in one section"): ONE front page. The memory
 * orb is on the board by default, the chat on the right is the REAL chat
 * (the front page's agent-chat store — history, sessions, composer; spoken
 * turns are filed into it and show as its tail while they are spoken), and
 * the old "chats", "deck" and "orb" sections all render this view:
 *
 *   ┌ gigi · voice bars · lamps · counter ── name · brain · switch · chrome ┐
 *   │ [MEMORY ORB —     (       ORB        )     [CHAT — the thread,       ] │
 *   │  3D, list,         state · TALK · mic · voice  typed and spoken,    ] │
 *   │  search, map ]                                 history, composer   ] │
 *   │ [log][turn][api][vitals]([outputs][runs] ≥1536 px)                 ] │
 *   └──────────────────────────────────────────────────────────────────────┘
 *
 * Narrower than 1280 px the board is two columns (orb over memory | chat),
 * and narrower than 1024 px one column — orb, chat, memory, instruments —
 * and it scrolls.
 *
 * HUD v2 (2026-10-11, owner brief "HUD Redesign"): the orb and the voice
 * controls are the stage's centre and get the room; the right column is the
 * live transcript (partial → final → streaming reply); the left column is
 * the project memory, searchable and actionable in place; the remaining
 * instruments sit on one short row underneath:
 *
 *   ┌ gigi · voice bars · lamps · counter ── name · brain · switch · chrome ┐
 *   │ dock │ [MEMORY —      (       ORB        )      [LIVE TRANSCRIPT —  ] │
 *   │      │  search,        state · TALK · mic · voice  partial, heard,   ] │
 *   │      │  projects ]                                 streaming reply   ] │
 *   │      │ [log]          [turn][api][vitals]         [outputs][runs]    ] │
 *   └──────┴───────────────────────────────────────────────────────────────┘
 *
 * Off the board since v2, each still one click away in the dock: the 3D wiki
 * map (the memory panel searches and opens notes; its WebGL scene was the
 * deck's heaviest part), the capture ledger, the terminals and the coding
 * grid. The history below describes the board before v2. You talk to it by voice; the typed composer lives
 * on the classic chat surface, not here (maintainer decision 2026-08-18).
 * The layout follows the maintainer's sketch of 2026-08-17 (photographed
 * rotated; read upright):
 *
 *   ┌ gigi · voice bars · lamps · counter ── name · brain · switch · chrome ┐
 *   │ dock │ [log — the     [response][api]        [ WIKI — 3D, tall    ] │
 *   │      │  terminal]     (      ORB      )       [                    ] │
 *   │      │ [outputs][run]   [capture][vitals]    [terminals] [ide grid] │
 *   └──────┴───────────────────────────────────────────────────────────────┘
 *
 * Two of the sketch's cards were re-thought on 2026-08-18 (maintainer): the
 * "right now" trace doubled the run inspector, which the small RUNS card
 * already opens, so the tall left slot is now the LOG — a terminal of what
 * the assistant heard, thought, did and said, with timings, that is never
 * blank; and the live Computer-Use screen mirror gave way to the RESPONSE
 * instrument — the turn's phases and how long each stage took. Computer Use
 * still shows: as the "control" lamp, as the response card's act phase, and
 * as lines in the log.
 *
 * Every card is a window onto a section and reads that section's own data;
 * its title jumps there. The dock on the left is the ONLY place the sections
 * are listed — the sidebar steps aside while the deck is up (App.tsx).
 *
 * The board is the deck's SECOND act (maintainer, 2026-08-18: a fresh start
 * showed nine instruments all saying "nothing yet"). While the app comes up
 * the stage is `DeckStandby` — the boot sequence — and the board takes over
 * the moment a turn opens or the person asks for it
 * (`lib/deckStandby.ts::resolvePhase`, forward only). It also takes over ON
 * ITS OWN, a blink after the gates are up (maintainer, 2026-08-20: opening the app must land you on the board,
 * never on a screen that asks you to speak first —
 * `lib/deckStandby.ts::AUTO_LAUNCH`). The start plays out in full either
 * way; only the trigger changed. The hand-off is a LAUNCH, on one clock
 * (`lib/deckStandby.ts::HANDOFF`): the orb flares and a shockwave leaves
 * it, the boot's ring bursts past the camera and its console gets out of
 * the way (`DeckStandby`), the orb travels from the ring's centre to its
 * place on the board (one `layoutId` on both stages) and lands with a ring,
 * the instruments assemble from the centre outward as the wave reaches them
 * (`DeckReveal`), and one scan runs down the whole board. The maintainer's
 * verdict on a fade-and-wipe (2026-08-19): a hard switch, ridiculous — this
 * has to be cinematic.
 *
 * The frames differ on purpose (HudFrame.tsx): brackets for pictures and the
 * map, chamfers for readouts, rails for streams — a deck of instruments, not
 * a grid of identical boxes. Nothing on this screen is invented: a number the
 * store cannot source is not shown.
 */
export function MissionDeckView({
  headerAccessory,
  dock = true,
}: {
  /** The surface switch, handed down by the shell that owns the mode. */
  headerAccessory?: ReactNode;
  /** Its own section dock; off where the app's sidebar already navigates. */
  dock?: boolean;
}) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const connected = useEventStore((s) => s.connected);
  const voiceReady = useEventStore((s) => s.voiceReady);
  const voiceState = useEventStore((s) => s.voiceState);
  const chatThinking = useEventStore((s) => s.chatThinking);
  const thinkingSteps = useEventStore((s) => s.thinkingSteps);
  const messages = useEventStore((s) => s.messages);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const cuActive = useDeckStore((s) => s.cu.active);
  const wordsSession = useDeckStore((s) => s.wordsSession);
  const turnPhase = useDeckStore((s) => s.turn.phase);
  const turnIndex = useDeckStore((s) => s.turn.index);
  const boardOpen = useDeckStore((s) => s.boardOpen);
  const openBoard = useDeckStore((s) => s.openBoard);
  const status = useAssistantStatus();
  const avatar = useDeckAvatar();
  // Header + orb name the engine that will answer the next spoken turn, not
  // the dormant sibling. Pipeline and realtime are independent picks; the
  // sidebar footer already followed this rule and the deck was still showing
  // the classic brain (OpenRouter) while Vertex AI Live was talking.
  const engine = useVoiceEngineDisplay();
  // The orb is the click-shaped wake word: the same start/stop path the
  // classic surface's voice bubble uses, so both do exactly one thing.
  const { active: callActive, busy: callBusy, connecting, toggleCall } = useVoiceCall();
  // The idle line names the real phrase ("Hey Nova"), never "your wake word":
  // a person who just installed the app should read what to say. Until the
  // phrase is known the name it derives from stands in.
  const { config: wakeConfig } = useWakeWord();
  const wakePhrase = wakeConfig?.phrase.trim() || assistantName;

  // Which act: the start sequence or the board — forward only for the session.
  const phase = resolvePhase({
    boardOpen,
    turnIndex,
    messageCount: messages.length,
    voiceEngaged: callActive || connecting,
  });
  // Forward only, for real: a transient reason for the board (a call being
  // set up, a voice state that came and went without a turn) must not let the
  // start sequence come back over a stage that already handed off — an interrupted
  // hand-off leaves the orb's shared-layout crossfade half-way, with the ring
  // back and nothing in it (seen 2026-08-19). So the first board render
  // latches the store's sticky flag.
  useEffect(() => {
    if (phase === "board") openBoard();
  }, [phase, openBoard]);
  // The start sequence launches itself. Nobody who opens the app wants a
  // screen that asks to be spoken to first (maintainer, 2026-08-20) — so the
  // boot lights its gates and a blink later the SAME hand-off a spoken word
  // triggers plays on its own (`lib/deckStandby.ts::AUTO_LAUNCH`). Any real
  // trigger arriving first simply wins: the phase is already board and this
  // timer never fires.
  useEffect(() => {
    if (phase === "board") return;
    const after = autoLaunchAfterMs({ connected, voiceReady });
    if (after === null) return;
    const id = window.setTimeout(() => openBoard(), after);
    return () => window.clearTimeout(id);
  }, [phase, connected, voiceReady, openBoard]);
  // The board powers on with its choreography ONLY when it takes over from
  // the standby on this very screen. A deck that mounts straight into a
  // running session (a section change and back) is simply there. Fixed on
  // the first render so the wrappers never remount because the flag moved.
  const mountedInto = useRef(phase);
  const revealBoard = mountedInto.current !== "board";
  // The board is a display case: the pointer sways its planes (lib/deckDepth).
  const boardRef = useRef<HTMLDivElement>(null);
  useDeckParallax(boardRef, phase === "board");
  const running = useMemo(
    () => thinkingSteps.filter((s) => s.status === "active"),
    [thinkingSteps],
  );
  // The deck's own turn counts too: the text chat's thinking flag never arms
  // for a voice turn, and the orb must sweep for those as well.
  const busy =
    chatThinking ||
    running.length > 0 ||
    voiceState === "thinking" ||
    turnPhase === "think" ||
    turnPhase === "act";

  // The header and the ring's readout say what the ONE status source says
  // (hooks/useAssistantStatus) — the sidebar dot and the banner read it too.
  // The deck used to derive its own mood from `connected` + `voiceState`
  // only, so it showed READY while the voice stack was still starting and
  // the stand-by beside it said "starting" (UI audit 2026-10-07, P0-1).
  // The deck's own turn signals (live steps, the turn card's phase) still
  // count as working while the shared status is at rest.
  const statusLabel =
    status.phase === "ready" && busy ? t("voice_state.thinking") : formatAssistantStatus(status, t);

  const orbPressLabel = callActive
    ? t("deck.orb_hangup")
    : t("deck.orb_call").replace("{0}", wakePhrase);
  const readouts: OrbReadouts = {
    nw: statusLabel,
    ne: `${running.length} ${t("deck.orb_steps")}`,
    sw: engine.providerLabel,
    se: `${wordsSession} ${t("deck.orb_words")}`,
  };
  // Pressing the orb reaches for the voice — the board opens on the press
  // itself, so the hand-off plays the moment the person acts, not a second
  // later when the transport reports back.
  //
  // A call that starts here continues the chat on the HUD: the backend is
  // told which chat is on screen first (`bindVoiceChat`, the same binding
  // the chat's own voice button makes), so the spoken turns are filed into
  // the thread beside the orb and the call starts with its history.
  const pressOrb = () => {
    openBoard();
    if (callActive || callBusy || connecting || !connected) {
      void toggleCall();
      return;
    }
    void bindVoiceChat(useAgentChatStore.getState().activeSessionId)
      .catch((err: unknown) => {
        // The call still starts; it just falls back to the newest chat.
        console.info("Voice chat binding failed.", err);
      })
      .finally(() => void toggleCall());
  };
  // The full memory map, opened over the stage from the memory panel.
  const [fullMap, setFullMap] = useState<FullMapRequest | null>(null);
  const pressDisabled = callBusy || connecting || !connected;

  return (
    <MotionConfig reducedMotion="user">
    <div className="flex h-full min-h-0 flex-col">
      {/* The deck's own instruments. Theme, restart and the window buttons
          live in the window caption, not in a second row here. */}
      <header className="relative flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2">
        <span
          className="flex h-8 w-8 shrink-0 items-center justify-center"
          data-testid="deck-header-gigi"
        >
          <PetMark size={32} reactive />
        </span>
        <div className="flex items-center gap-3">
          <VoiceWaveform
            levelRef={voiceInputLevelRef}
            phase={waveformPhase(voiceState, connected)}
            count={14}
            className="h-6"
          />
          <span
            className={cn(
              "font-mono text-micro uppercase tracking-[0.18em]",
              status.tone === "error" ? "text-destructive" : "text-primary",
            )}
            data-testid="deck-status-label"
            data-phase={status.phase}
          >
            {statusLabel}
          </span>
        </div>

        {/* Lamp row: the things that have to be true for a voice turn. */}
        <div className="flex items-center gap-3 font-mono text-micro uppercase tracking-[0.14em] text-muted-foreground">
          <Lamp on={connected} label={t("deck.lamp_link")} />
          <Lamp on={voiceReady} label={t("deck.lamp_voice")} />
          <Lamp
            on={engine.providerLabel !== "—"}
            label={t(engine.tier === "realtime" ? "deck.stat_realtime" : "deck.lamp_brain")}
          />
          <Lamp on={cuActive} label={t("deck.lamp_cu")} />
        </div>

        <LiveCounter />

        <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="font-display text-sm font-bold uppercase tracking-[0.18em]">
            {assistantName}
          </span>
          <HeaderStat
            label={t(engine.tier === "realtime" ? "deck.stat_realtime" : "deck.stat_brain")}
            value={engine.providerLabel}
            hot
            testId="deck-stat-engine"
            onClick={() => setActiveSection("apikeys")}
          />
          <HeaderStat
            label={t("deck.stat_model")}
            value={engine.model || "—"}
            testId="deck-stat-model"
            onClick={() => setActiveSection("apikeys")}
          />
          <HeaderStat
            label={t("deck.stat_avatar")}
            value={t(`deck.avatar_${avatar}`)}
            testId="deck-stat-avatar"
            onClick={() => writeDeckAvatar(avatar === "gigi" ? "reactor" : "gigi")}
          />
          {headerAccessory}
        </div>

        <svg
          className="pointer-events-none absolute inset-x-3 bottom-0 h-2 w-[calc(100%-1.5rem)]"
          preserveAspectRatio="none"
          viewBox="0 0 100 8"
          aria-hidden
        >
          <path
            d="M 0 0 V 8 M 0 7.5 H 100 M 100 0 V 8"
            fill="none"
            stroke="hsl(var(--primary))"
            strokeWidth={1}
            opacity={0.5}
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      </header>

      {/* Stage */}
      <div className="flex min-h-0 flex-1">
        {dock && <DockRail />}

        <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
          {phase === "board" && (
            <div
              ref={boardRef}
              data-testid="deck-board"
              className={cn(
                // overflow-x stays hidden: the planes' depth transforms reach a few
                // pixels past the board's edge, and must not buy it a sideways scrollbar.
                "deck-board-3d relative grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto overflow-x-hidden p-3",
                // Two columns from a laptop-narrow stage up: the orb over the
                // memory on the left, the chat down the right; the page scrolls.
                "lg:grid-cols-2",
                // Three from 1280 px: memory | orb | chat, on one screen with
                // nothing to scroll. The chat runs the full height — it is the
                // one panel that is read line by line.
                "xl:grid-cols-[minmax(240px,3fr)_minmax(0,4.4fr)_minmax(320px,4fr)] xl:grid-rows-[minmax(0,1fr)_minmax(0,0.34fr)] xl:overflow-hidden",
              )}
            >
              {/* The floor the case stands on — behind every plane, on the wall's side. */}
              <DeckFloor />

              {/* LEFT top: the memory orb — everything remembered in 3D,
                  following the conversation (MemoryOrb3D); the list, the
                  search, the full map and the data import are in its title
                  strip. On a narrow window the orb, the voice and the chat
                  come first. */}
              <DeckReveal
                slot="left-top"
                reveal={revealBoard}
                className={cn(
                  "order-3 min-h-[24rem] xl:order-none xl:col-start-1 xl:row-start-1 xl:min-h-0",
                  depthSlot("left-top").className,
                )}
                style={depthSlot("left-top").style}
                bodyClassName="flex min-h-0 flex-col"
              >
                <HudMemoryPanel className="min-h-0 flex-1" onOpenMap={setFullMap} />
              </DeckReveal>

              {/* CENTRE: the orb on its stage, the controls under it — the
                  largest, emptiest region of the board on purpose. */}
              <div className="order-1 flex min-h-[28rem] flex-col gap-2 xl:order-none xl:col-start-2 xl:row-start-1 xl:min-h-0">
                <BoardCentre
                  steps={running}
                  busy={busy}
                  readouts={readouts}
                  onPress={pressOrb}
                  pressLabel={orbPressLabel}
                  pressDisabled={pressDisabled}
                  reveal={revealBoard}
                />
                <DeckReveal
                  slot="centre-top"
                  reveal={revealBoard}
                  className={cn("shrink-0", depthSlot("centre-top").className)}
                  style={depthSlot("centre-top").style}
                  bodyClassName="flex justify-center pb-1"
                >
                  <HudControls
                    status={status}
                    statusLabel={statusLabel}
                    callActive={callActive}
                    busy={callBusy}
                    connecting={connecting}
                    disabled={!connected}
                    onTalk={pressOrb}
                    wakePhrase={wakePhrase}
                  />
                </DeckReveal>
              </div>

              {/* RIGHT, full height: the chat — the real one, its history and
                  its composer, with the spoken turn in flight as its tail. */}
              <DeckReveal
                slot="right-top"
                reveal={revealBoard}
                className={cn(
                  "order-2 min-h-[30rem] lg:row-span-2 xl:order-none xl:col-start-3 xl:row-span-2 xl:row-start-1 xl:min-h-0",
                  flatSlot("right-top").className,
                )}
                style={flatSlot("right-top").style}
                bodyClassName="flex min-h-0 flex-col"
              >
                <HudChatPanel className="min-h-0 flex-1" />
              </DeckReveal>

              {/* BOTTOM, under the memory and the orb: the instruments. Below
                  1280 px the row dissolves (`contents`) and each instrument
                  takes its own place in the stack. */}
              <div className="contents xl:col-span-2 xl:col-start-1 xl:row-start-2 xl:flex xl:min-h-0 xl:gap-3">
                {/* The log — the terminal of the session */}
                <DeckReveal
                  slot="left-bottom"
                  reveal={revealBoard}
                  className={cn("order-4 min-h-[8rem] xl:order-none xl:min-h-0 xl:min-w-0 xl:flex-[2]", flatSlot("left-bottom").className)}
                  style={flatSlot("left-bottom").style}
                  bodyClassName="flex min-h-0 flex-col"
                >
                  <LogCard className="min-h-0 flex-1" />
                </DeckReveal>

                {/* The turn's timings, what it cost, the machine */}
                <DeckReveal
                  slot="centre-bottom"
                  reveal={revealBoard}
                  className={cn("order-5 min-h-[8rem] xl:order-none xl:min-h-0 xl:min-w-0 xl:flex-[3.2]", flatSlot("centre-bottom").className)}
                  style={flatSlot("centre-bottom").style}
                  bodyClassName="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"
                >
                  <TurnCard className="min-h-0" />
                  <ApiStatsCard className="min-h-0" />
                  <VitalsCard className="min-h-0" />
                  <MissionProgressCard steps={thinkingSteps} className="min-h-0" />
                </DeckReveal>

                {/* Outputs and runs. Between 1280 and 1536 px the row has no
                    room left for them; Work (Artifacts, Runs) has both. */}
                <DeckReveal
                  slot="right-bottom"
                  reveal={revealBoard}
                  className={cn("order-6 min-h-[8rem] xl:hidden 2xl:flex xl:order-none 2xl:min-h-0 2xl:min-w-0 2xl:flex-[2]", flatSlot("right-bottom").className)}
                  style={flatSlot("right-bottom").style}
                  bodyClassName="grid grid-cols-2 gap-3"
                >
                  <OutputsCard className="min-h-0" />
                  <RunsCard className="min-h-0" />
                </DeckReveal>
              </div>

              {/* The launch's last beat: one scan down the whole board. */}
              {revealBoard && <BoardSweep />}
            </div>
          )}

          {fullMap && <FullMemoryMap request={fullMap} onClose={() => setFullMap(null)} />}

          {/* Before the board: the start sequence. Absolute over the stage
              so its exit plays over the board powering on underneath. */}
          <AnimatePresence>
            {phase !== "board" && (
              <DeckStandby
                key="standby"
                className="absolute inset-0 z-10"
                steps={running}
                busy={busy}
                readouts={readouts}
                wakeConfig={wakeConfig}
                onPressOrb={pressOrb}
                pressLabel={orbPressLabel}
                pressDisabled={pressDisabled}
                onOpenBoard={openBoard}
              />
            )}
          </AnimatePresence>
        </div>
      </div>

    </div>
    </MotionConfig>
  );
}


/**
 * The full memory map over the HUD's stage: everything the memory-orb page
 * had — the projects and memory maps, hand control, the data import — one
 * button away from the memory panel, closed with the X or Escape. The HUD
 * stays mounted underneath, so closing it lands exactly where you were.
 */
function FullMemoryMap({ request, onClose }: { request: FullMapRequest; onClose: () => void }) {
  const t = useT();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("hud3.memory.map_title")}
      data-testid="hud-full-map"
      className="absolute inset-0 z-30 flex flex-col bg-background"
    >
      <Suspense
        fallback={
          <div role="status" className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            {t("common.loading")}
          </div>
        }
      >
        <MemoryOrbView initialMode={request.mode} importOpen={request.importOpen} />
      </Suspense>
      <button
        type="button"
        onClick={onClose}
        data-testid="hud-full-map-close"
        aria-label={t("hud3.memory.map_close")}
        title={t("hud3.memory.map_close")}
        className="absolute bottom-4 right-4 z-40 flex items-center gap-1.5 border border-primary/50 bg-background/80 px-2.5 py-1.5 font-mono text-micro uppercase tracking-[0.16em] text-primary backdrop-blur transition-colors hover:bg-primary/10"
      >
        <X className="h-3.5 w-3.5" aria-hidden />
        {t("hud3.memory.map_close")}
      </button>
    </div>
  );
}

/**
 * A slot's place in the display case (lib/deckDepth.ts) as the props its
 * DeckReveal wrapper takes: the transform rule's class, the depth as custom
 * properties, and `deck-slot-far` for planes behind the wall so their cards
 * read softer.
 */
function depthSlot(slot: BoardSlot): { className: string; style: CSSProperties } {
  return {
    className: cn("deck-slot-3d", SLOT_DEPTH[slot].z < 0 && "deck-slot-far"),
    style: slotDepthVars(slot) as CSSProperties,
  };
}

/**
 * A plane on the wall itself — flat, unturned — keeping only its parallax
 * (HUD v3). The instrument strip (log, turn/api/vitals, outputs/runs) shares
 * ONE row under the memory and the orb now, so it stands together instead
 * of each card on its own depth, where the near log grew into the memory
 * panel above it. The chat stands here too: it is read line by line, and
 * text on a plane turned away from the eye, behind the wall's softer glass,
 * reads worse than text facing it.
 */
function flatSlot(slot: BoardSlot): { className: string; style: CSSProperties } {
  return {
    className: "deck-slot-3d",
    style: {
      ...slotDepthVars(slot),
      "--slot-z": "0px",
      "--slot-ry": "0deg",
      "--slot-scale": "1",
    } as CSSProperties,
  };
}


/** The HUD orb's bounds: below the minimum the readouts collide, above the
 * maximum the reticle stops reading as an instrument and starts reading as
 * wallpaper. */
const HUD_ORB_MIN = 200;
const HUD_ORB_MAX = 440;

/** The reticle size for the centre stage — as large as the room allows. */
function hudOrbSize(width: number, height: number): number {
  if (!width || !height) return 320;
  const fit = Math.min(width - 24, height - 16);
  return Math.max(HUD_ORB_MIN, Math.min(HUD_ORB_MAX, Math.floor(fit)));
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
}: {
  steps: ThinkingStep[];
  busy: boolean;
  readouts: OrbReadouts;
  onPress: () => void;
  pressLabel: string;
  pressDisabled: boolean;
  reveal: boolean;
}) {
  const stageRef = useRef<HTMLDivElement>(null);
  const stage = useElementSize(stageRef);
  const orbSize = hudOrbSize(stage.width, stage.height);
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

function waveformPhase(state: VoiceState, connected: boolean): WaveformPhase {
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

function Lamp({ on, label }: { on: boolean; label: string }) {
  return (
    <span className="flex items-center gap-1">
      <HudLamp on={on} />
      <span className={on ? "text-foreground/80" : undefined}>{label}</span>
    </span>
  );
}

function HeaderStat({
  label,
  value,
  hot,
  onClick,
  testId,
}: {
  label: string;
  value: string;
  hot?: boolean;
  onClick?: () => void;
  testId?: string;
}) {
  const body = (
    <>
      <span className="font-mono text-micro uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </span>
      <span className={cn("font-mono text-xs tabular-nums", hot ? "text-primary" : "text-foreground")}>
        {value}
      </span>
    </>
  );
  if (!onClick) {
    return (
      <div data-testid={testId} className="flex items-baseline gap-1.5 whitespace-nowrap">
        {body}
      </div>
    );
  }
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      className="flex items-baseline gap-1.5 whitespace-nowrap transition-colors hover:[&>span]:text-primary"
    >
      {body}
    </button>
  );
}

/**
 * The escape hatch, rendered by the shell above both surfaces.
 *
 * Kept in this module so the deck and the switch that leaves it stay together.
 */
export function SurfaceSwitch({
  mode,
  onChange,
}: {
  mode: "deck" | "classic";
  onChange: (mode: "deck" | "classic") => void;
}) {
  const t = useT();
  const next = mode === "deck" ? "classic" : "deck";
  const label = t(next === "classic" ? "deck.switch_to_classic" : "deck.switch_to_deck");

  return (
    <button
      type="button"
      onClick={() => {
        writeDeckMode(next);
        onChange(next);
      }}
      title={label}
      className="flex items-center gap-1.5 border border-border px-2 py-1 font-mono text-micro uppercase tracking-[0.12em] text-muted-foreground transition-colors hover:border-primary/50 hover:text-primary"
      style={{ clipPath: "polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px)" }}
    >
      {label}
    </button>
  );
}
