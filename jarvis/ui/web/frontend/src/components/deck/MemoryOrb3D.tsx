import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { useReducedMotion } from "framer-motion";
import { ArrowUpRight, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";

import { useDeckSlotPowered } from "@/components/deck/DeckReveal";
import { fetchOrbTree } from "@/components/deck/hudMemoryApi";
import { useCanvasAwake } from "@/hooks/useCanvasAwake";
import { useWebglSupported } from "@/lib/graphDimension";
import type { TranscriptLine } from "@/lib/homeTranscript";
import {
  ORB_HEX,
  capLeaves,
  layoutOrb,
  nodeNamedIn,
  stepCommunity,
  stepLeaf,
  type OrbColour,
  type OrbRef,
  type PlacedNode,
} from "@/lib/memoryOrbLayout";
import { cn } from "@/lib/utils";
import { useHomeStore } from "@/store/home";
import { fill, useT } from "@/i18n";

/** The scene is three.js: its own chunk, fetched once the orb is actually shown. */
const MemoryOrbScene = lazy(() => import("@/components/deck/MemoryOrbScene"));

export const ORB_TREE_KEY = ["hud2", "memory", "orb-tree"] as const;
/** Most leaves drawn; past this, communities share the room (`capLeaves`). */
export const MAX_ORB_LEAVES = 600;
const COLOURS: OrbColour[] = ["red", "yellow", "blue", "green", "reference"];

/** Where the current focus came from — the conversation, the steppers, or a click. */
type FocusSource = "voice" | "step" | "pick";

/** The newest settled line someone SAID (user or assistant); step rows are skipped. */
export function lastSpokenLine(transcript: TranscriptLine[]): TranscriptLine | null {
  for (let i = transcript.length - 1; i >= 0; i--) {
    const line = transcript[i];
    if (line.who !== "steps" && line.text.trim()) return line;
  }
  return null;
}

/**
 * The memory orb on the HUD: everything remembered, as GraphRAG groups it
 * (`/api/wiki/orb-tree`, `jarvis/memory/orb_tree.py`), on three shells
 * around a core (`lib/memoryOrbLayout.ts`), coloured by the work done.
 *
 * It follows the conversation: when a settled transcript line — the
 * person's words or the assistant's answer, from the same home-store lane
 * the transcript panel reads — names a project or a note, the orb turns to
 * it. The arrows step through the focused community's notes (and the
 * double arrows from community to community), so the orb can be read one
 * note at a time by hand or keyboard as well.
 *
 * Opening a node hands its reference to the memory panel around it, which
 * already knows how to show a project, an item and a note — the orb never
 * grows a second detail view.
 *
 * Without WebGL the frame says so in words; the legend, the focus line and
 * the steppers keep working.
 */
export function MemoryOrb3D({
  className,
  onOpen,
}: {
  className?: string;
  /** Open a node's project, item or note (the memory panel's detail view). */
  onOpen?: (ref: OrbRef) => void;
}) {
  const t = useT();
  const hostRef = useRef<HTMLDivElement>(null);
  const awake = useCanvasAwake(hostRef);
  const powered = useDeckSlotPowered();
  const webgl = useWebglSupported();
  const reduced = useReducedMotion() ?? false;
  const [lost, setLost] = useState(false);

  const tree = useQuery({
    queryKey: ORB_TREE_KEY,
    queryFn: ({ signal }) => fetchOrbTree(signal),
    staleTime: 60_000,
    refetchInterval: 120_000,
    retry: false,
  });

  const leafTotal = tree.data?.leaves.length ?? 0;
  const layout = useMemo(
    () =>
      layoutOrb({
        communities: tree.data?.communities ?? [],
        leaves: capLeaves(tree.data?.leaves ?? [], MAX_ORB_LEAVES),
      }),
    [tree.data],
  );

  const [focus, setFocusState] = useState<{ id: string; source: FocusSource } | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const focusTo = useCallback((id: string | null, source: FocusSource) => {
    setFocusState(id ? { id, source } : null);
  }, []);
  // A refetch that dropped the focused node drops the focus with it.
  useEffect(() => {
    if (focus && !layout.byId.has(focus.id)) setFocusState(null);
  }, [layout, focus]);

  // Streams through notes with the conversation. Only lines that arrive (or
  // grow) after the orb is up count: opening the panel never jumps to
  // something said an hour ago. One line moves the focus once per node, so
  // an answer that keeps streaming does not keep yanking the camera back.
  const transcript = useHomeStore((s) => s.transcript);
  const spoken = lastSpokenLine(transcript);
  const startKey = useRef(spoken ? `${spoken.id}:${spoken.text}` : "");
  const voiceMark = useRef("");
  useEffect(() => {
    if (!spoken || layout.nodes.length === 0) return;
    if (`${spoken.id}:${spoken.text}` === startKey.current) return;
    const node = nodeNamedIn(layout, spoken.text);
    if (!node) return;
    const mark = `${spoken.id}:${node.id}`;
    if (mark === voiceMark.current) return;
    voiceMark.current = mark;
    focusTo(node.id, "voice");
  }, [spoken, layout, focusTo]);

  const focused = focus ? (layout.byId.get(focus.id) ?? null) : null;
  const hovered = hoverId ? (layout.byId.get(hoverId) ?? null) : null;
  const shown = hovered ?? focused;

  const stepNote = (delta: number) => {
    const next = focused
      ? (stepLeaf(layout, focused.id, delta) ?? stepCommunity(layout, focused.id, delta))
      : stepCommunity(layout, null, delta);
    if (next) focusTo(next.id, "step");
  };
  const stepGroup = (delta: number) => {
    const next = stepCommunity(layout, focused?.id ?? null, delta);
    if (next) focusTo(next.id, "step");
  };

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const keys: Record<string, () => void> = {
      ArrowRight: () => stepNote(1),
      ArrowLeft: () => stepNote(-1),
      ArrowDown: () => stepGroup(1),
      ArrowUp: () => stepGroup(-1),
      Escape: () => focusTo(null, "pick"),
      Enter: () => focused?.ref && onOpen?.(focused.ref),
    };
    const run = keys[e.key];
    if (!run) return;
    e.preventDefault();
    run();
  };

  const unavailable = tree.data?.unavailable ?? [];
  const state = tree.isLoading
    ? "loading"
    : tree.isError
      ? "error"
      : layout.nodes.length === 0
        ? unavailable.length > 0
          ? "error"
          : "empty"
        : "ready";

  if (state !== "ready") {
    return (
      <div
        ref={hostRef}
        data-testid="memory-orb"
        data-state={state}
        className={cn("flex min-h-0 flex-1 items-center justify-center px-4 text-center", className)}
      >
        <p data-testid="memory-orb-state" className="text-xs leading-relaxed text-muted-foreground">
          {t(`hud2.orb.${state}`)}
        </p>
      </div>
    );
  }

  const drawn = webgl && !lost;
  return (
    <div
      ref={hostRef}
      data-testid="memory-orb"
      data-state={state}
      data-renderer={drawn ? "webgl" : "none"}
      data-focus={focused?.id ?? ""}
      tabIndex={0}
      onKeyDown={onKey}
      aria-label={t("hud2.orb.label")}
      className={cn("relative flex min-h-0 flex-1 flex-col focus-visible:outline-none", className)}
    >
      {/* Legend: what each colour means and how many leaves carry it; under
          it, any store the route could not read. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex flex-col gap-0.5 px-1 font-mono text-[10px] text-muted-foreground">
        <div className="flex flex-wrap gap-x-2.5 gap-y-0.5">
          {COLOURS.filter((c) => (tree.data?.counts[c] ?? 0) > 0).map((c) => (
            <span key={c} className="pointer-events-auto flex items-center gap-1" title={t(`hud2.orb.legend_hint.${c}`)}>
              <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: ORB_HEX[c] }} />
              {t(`hud2.orb.legend.${c}`)}
              <span className="tabular-nums opacity-70">{tree.data?.counts[c]}</span>
            </span>
          ))}
          {leafTotal > MAX_ORB_LEAVES && (
            <span data-testid="memory-orb-capped" className="opacity-70">
              {fill(t("hud2.orb.capped"), { shown: MAX_ORB_LEAVES, total: leafTotal })}
            </span>
          )}
        </div>
        {unavailable.length > 0 && (
          <p data-testid="memory-orb-unavailable" className="font-sans text-warning">
            {unavailable.map((what) => t(what === "work" ? "hud2.orb.unavailable_work" : "hud2.orb.unavailable_notes")).join(" ")}
          </p>
        )}
      </div>

      <div className="relative min-h-0 flex-1">
        {drawn ? (
          powered && (
            <Suspense fallback={<Centered>{t("hud2.orb.loading")}</Centered>}>
              <MemoryOrbScene
                layout={layout}
                focusedId={focused?.id ?? null}
                hoverId={hoverId}
                onHover={setHoverId}
                onPick={(id) => focusTo(id, "pick")}
                awake={awake}
                reduced={reduced}
                onUnavailable={() => setLost(true)}
              />
            </Suspense>
          )
        ) : (
          <Centered testId="memory-orb-fallback">{t("hud2.orb.no_webgl")}</Centered>
        )}
      </div>

      {/* The focus line: what is under the pointer or in focus, and the steppers. */}
      <div className="flex shrink-0 items-center gap-1.5 border-t border-accent/20 pt-1.5">
        <StepButton icon={ChevronsLeft} label={t("hud2.orb.prev_group")} onClick={() => stepGroup(-1)} />
        <StepButton icon={ChevronLeft} label={t("hud2.orb.prev")} onClick={() => stepNote(-1)} testId="memory-orb-prev" />
        <div className="flex min-w-0 flex-1 flex-col" aria-live="polite">
          {shown ? (
            <>
              <span data-testid="memory-orb-title" className="flex min-w-0 items-center gap-1.5 text-xs text-foreground">
                <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: ORB_HEX[shown.colour] }} />
                <span className="truncate">{shown.title}</span>
              </span>
              <span className="truncate font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                {t(`hud2.orb.level_${shown.level}`)} · {t(`hud2.orb.legend.${shown.colour}`)}
                {shown === focused && focus?.source === "voice" && ` · ${t("hud2.orb.from_voice")}`}
              </span>
            </>
          ) : (
            <span className="truncate text-[11px] text-muted-foreground">{t("hud2.orb.hint")}</span>
          )}
        </div>
        {focused?.ref && onOpen && (
          <OpenButton node={focused} label={t("hud2.orb.open")} onOpen={onOpen} />
        )}
        <StepButton icon={ChevronRight} label={t("hud2.orb.next")} onClick={() => stepNote(1)} testId="memory-orb-next" />
        <StepButton icon={ChevronsRight} label={t("hud2.orb.next_group")} onClick={() => stepGroup(1)} />
      </div>
    </div>
  );
}

function OpenButton({ node, label, onOpen }: { node: PlacedNode; label: string; onOpen: (ref: OrbRef) => void }) {
  return (
    <button
      type="button"
      data-testid="memory-orb-open"
      onClick={() => node.ref && onOpen(node.ref)}
      className="flex shrink-0 items-center gap-1 border border-accent/60 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-accent transition-colors hover:bg-accent/10"
    >
      <ArrowUpRight className="h-3 w-3" />
      {label}
    </button>
  );
}

function StepButton({
  icon: Icon,
  label,
  onClick,
  testId,
}: {
  icon: typeof ChevronLeft;
  label: string;
  onClick: () => void;
  testId?: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      title={label}
      aria-label={label}
      className="flex h-5 w-5 shrink-0 items-center justify-center border border-border/70 text-muted-foreground transition-colors hover:border-accent/60 hover:text-accent"
    >
      <Icon className="h-3 w-3" />
    </button>
  );
}

function Centered({ children, testId }: { children: string; testId?: string }) {
  return (
    <div
      data-testid={testId}
      className="absolute inset-0 flex items-center justify-center px-4 text-center text-xs leading-relaxed text-muted-foreground"
    >
      {children}
    </div>
  );
}
