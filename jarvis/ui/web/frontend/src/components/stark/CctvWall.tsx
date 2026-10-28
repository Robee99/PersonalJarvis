import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { fill, useT } from "@/i18n";
import { frameUrl, type CctvCamera } from "@/lib/cctv";
import { cn } from "@/lib/utils";

/**
 * GOD'S EYE VIEW FRAMES: one public road or city camera's latest still,
 * loaded straight from the local God's Eye View server and loaded again on
 * every `tick`. The Ghost screen's camera wall is built from these.
 */

/** A counter that goes up every `ms` while the page is visible and `on`. */
export function useFrameTick(ms: number, on = true): number {
  const [tick, setTick] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    const id = window.setInterval(() => {
      if (document.visibilityState !== "hidden") setTick(Date.now());
    }, ms);
    return () => window.clearInterval(id);
  }, [ms, on]);
  return tick;
}

/**
 * Cameras whose frame failed to load this many times in a row count as
 * showing no picture; the wall leaves a city whose cameras all do so out of
 * its place list. A frame that loads clears the count.
 */
export const DEAD_AFTER = 2;
const failures = new Map<string, number>();
let dead: ReadonlySet<string> = new Set();
const listeners = new Set<() => void>();
let notifyTimer: number | null = null;

function publishDead() {
  const next = new Set<string>();
  for (const [id, n] of failures) if (n >= DEAD_AFTER) next.add(id);
  if (next.size === dead.size && [...next].every((id) => dead.has(id))) return;
  dead = next;
  for (const fn of listeners) fn();
}

/** Record whether a camera's frame loaded. Changes reach listeners at most once a second. */
export function reportFrame(id: string, ok: boolean) {
  const before = failures.get(id) ?? 0;
  if (ok) {
    if (!before) return;
    failures.delete(id);
  } else {
    failures.set(id, before + 1);
  }
  if (notifyTimer === null) {
    notifyTimer = window.setTimeout(() => {
      notifyTimer = null;
      publishDead();
    }, 1000);
  }
}

/** Forget every recorded failure. Tests only. */
export function resetFrameReports() {
  failures.clear();
  dead = new Set();
  if (notifyTimer !== null) window.clearTimeout(notifyTimer);
  notifyTimer = null;
  for (const fn of listeners) fn();
}

/** The cameras whose frames keep failing. */
export function useDeadCameras(): ReadonlySet<string> {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    () => dead,
  );
}

/** One camera frame; says so when the frame did not load. */
export function CctvFrame({
  gev,
  camera,
  tick,
  className,
  onOpen,
  load = true,
  fit = "cover",
  label = true,
  coords = true,
  children,
}: {
  gev: string;
  camera: CctvCamera;
  tick: number;
  className?: string;
  onOpen?: () => void;
  /** False holds the image back (the wall is still scrolling); the tile keeps its place. */
  load?: boolean;
  fit?: "cover" | "contain";
  /** The camera's name in the corner. */
  label?: boolean;
  /** Its position in the other corner. */
  coords?: boolean;
  /** Extra overlay (a caption). */
  children?: ReactNode;
}) {
  const t = useT();
  const [failed, setFailed] = useState(false);
  const src = load ? frameUrl(gev, camera.id, tick) : null;
  useEffect(() => setFailed(false), [src]);
  const body = (
    <>
      {failed ? (
        <span className="absolute inset-0 flex items-center justify-center p-2 text-center text-[10px] text-muted-foreground" data-testid="stark-cctv-noframe">
          {t("stark.cctv.no_frame")}
        </span>
      ) : src ? (
        <img
          src={src}
          alt={fill(t("stark.cctv.frame_alt"), { camera: camera.name, city: camera.city })}
          className={cn("absolute inset-0 h-full w-full", fit === "contain" ? "object-contain" : "object-cover")}
          loading="lazy"
          decoding="async"
          onLoad={() => reportFrame(camera.id, true)}
          onError={() => {
            setFailed(true);
            reportFrame(camera.id, false);
          }}
          data-testid="stark-cctv-frame"
          data-camera={camera.id}
        />
      ) : null}
      {label && (
        <span className="pointer-events-none absolute left-1 top-1 flex max-w-[calc(100%-0.5rem)] items-center gap-1 bg-background/80 px-1.5 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-[0.12em] text-foreground">
          <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-[hsl(var(--stark-hot))]" />
          <span className="truncate">{camera.name}</span>
        </span>
      )}
      {coords && (
        <span className="pointer-events-none absolute bottom-1 right-1 bg-background/70 px-1 font-mono text-[8px] tabular-nums text-muted-foreground">
          {camera.lat.toFixed(3)}, {camera.lon.toFixed(3)}
        </span>
      )}
      {children}
    </>
  );
  const box = cn("relative block min-h-0 min-w-0 overflow-hidden border border-primary/15 bg-black", className);
  return onOpen ? (
    <button type="button" onClick={onOpen} className={cn(box, "text-left hover:border-primary/60 focus-visible:border-primary focus-visible:outline-none")} aria-label={fill(t("stark.cctv.open"), { camera: camera.name })}>
      {body}
    </button>
  ) : (
    <div className={box}>{body}</div>
  );
}
