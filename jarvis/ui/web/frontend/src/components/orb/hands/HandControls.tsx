/**
 * The Orb's hand-control toggle, its status, the gesture guide, the hand
 * cursors and the live gesture indicator.
 *
 * Off by default: the camera only starts when the person turns hands on, and
 * goes off with the toggle or the view. Whatever the state, the mouse,
 * keyboard and touch keep working; the panel says so, and says exactly why
 * hands are not available when they are not.
 *
 * While hands are live the panel lists every gesture (pose → what it does)
 * and lights up the one the camera recognises; the indicator over the map
 * names it too, fills while a held gesture charges, and briefly names what
 * the last gesture did.
 */
import { useEffect, useRef, useState } from "react";
import { Hand, Loader2 } from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { GESTURES, type GestureId, type HandCursor, type LiveGesture } from "./gestures";
import { HAND_PROBLEMS, type HandTracking } from "./useHandTracking";

const BUSY = new Set(["checking", "installing", "loading", "camera"]);

/** Which guide rows a live gesture lights up ("pinch" is both a tap and a hold). */
export function guideRowsFor(gesture: LiveGesture | null): GestureId[] {
  if (!gesture) return [];
  return gesture.id === "pinch" ? ["select", "open"] : [gesture.id];
}

/** Every gesture, what it does, and which one the camera sees now. */
export function HandGuide({
  gesture,
  available = GESTURES,
}: {
  gesture: LiveGesture | null;
  available?: readonly GestureId[];
}) {
  const t = useT();
  const lit = guideRowsFor(gesture);
  return (
    <div data-testid="orb-hands-guide">
      <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground">{t("orb2.hands.guide_title")}</p>
      <ul className="mt-1 space-y-0.5">
        {GESTURES.map((id) => {
          const on = lit.includes(id);
          const usable = available.includes(id);
          return (
            <li
              key={id}
              data-gesture={id}
              data-active={on || undefined}
              className={cn(
                "grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2 rounded-md px-1.5 py-1 leading-4 transition-colors",
                on ? "bg-info/15 text-foreground" : "text-muted-foreground",
                !usable && "opacity-50",
              )}
            >
              <span className={cn("font-medium", on ? "text-info" : "text-foreground")}>{t(`orb2.hands.guide.${id}.pose`)}</span>
              <span>{usable ? t(`orb2.hands.guide.${id}.action`) : t("orb2.hands.guide_unavailable")}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function HandToggle({
  tracking,
  className,
  gestures,
}: {
  tracking: HandTracking;
  className?: string;
  /** The gestures the map shown can act on; the rest are listed as unavailable. */
  gestures?: readonly GestureId[];
}) {
  const t = useT();
  const { status, detail } = tracking;
  const on = status !== "off";
  const busy = BUSY.has(status);
  const problem = HAND_PROBLEMS.includes(status);
  return (
    <div className={cn("relative", className)}>
      <button
        type="button"
        aria-pressed={on}
        data-testid="orb-hands-toggle"
        title={t("orb2.hands.toggle_title")}
        onClick={() => (on ? tracking.stop() : tracking.start())}
        className={cn(
          "pointer-events-auto flex h-9 items-center gap-1.5 rounded-full border bg-black/50 px-3 font-mono text-[11px] uppercase tracking-[0.18em] backdrop-blur transition-colors",
          status === "live"
            ? "border-amber-300/70 text-amber-200 shadow-[0_0_18px_rgba(255,193,69,0.25)]"
            : "border-cyan-400/20 text-slate-200 hover:border-cyan-300/60",
        )}
      >
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <Hand className="h-3.5 w-3.5" aria-hidden />}
        {t("orb2.hands.toggle")}
        <span
          aria-hidden
          className={cn(
            "h-1.5 w-1.5 rounded-full",
            status === "live" ? "bg-amber-300" : problem ? "bg-rose-400" : busy ? "bg-cyan-300" : "bg-slate-600",
          )}
        />
      </button>
      {on && (
        <div
          role="status"
          data-testid="orb-hands-status"
          className="pointer-events-auto absolute right-0 top-11 z-20 max-h-[calc(100vh-8rem)] w-80 overflow-y-auto rounded-xl border border-border bg-background/90 p-3 text-xs text-foreground shadow-float backdrop-blur"
        >
          <p className={cn("font-mono text-[10px] uppercase tracking-[0.2em]", problem ? "text-destructive" : "text-info")}>
            {t(`orb2.hands.status.${status}`)}
          </p>
          {problem && <p className="mt-1.5 leading-5">{t(`orb2.hands.why.${status}`)}</p>}
          {detail && problem && <p className="mt-1 break-words font-mono text-[10px] text-muted-foreground">{detail}</p>}
          {status === "live" && (
            <div className="mt-2">
              <HandGuide gesture={tracking.gesture ?? null} available={gestures} />
            </div>
          )}
          <div className="mt-2 flex flex-wrap gap-1.5">
            {status === "files-missing" && (
              <button
                type="button"
                data-testid="orb-hands-install"
                onClick={() => tracking.start({ install: true })}
                className="rounded-full bg-info/15 px-2.5 py-1 text-foreground hover:bg-info/25"
              >
                {t("orb2.hands.install")}
              </button>
            )}
            {problem && status !== "files-missing" && status !== "unsupported" && (
              <button
                type="button"
                data-testid="orb-hands-retry"
                onClick={() => tracking.start({ install: status === "install-failed" })}
                className="rounded-full bg-info/15 px-2.5 py-1 text-foreground hover:bg-info/25"
              >
                {t("orb2.hands.retry")}
              </button>
            )}
            <button
              type="button"
              onClick={() => tracking.stop()}
              className="rounded-full border border-border px-2.5 py-1 text-foreground hover:border-border-strong"
            >
              {t(problem ? "orb2.hands.use_mouse" : "orb2.hands.turn_off")}
            </button>
          </div>
          <p className="mt-2 text-[10px] leading-4 text-muted-foreground">
            {t(problem ? "orb2.hands.fallback" : "orb2.hands.privacy")}
          </p>
        </div>
      )}
    </div>
  );
}

const POSE_RING: Record<HandCursor["pose"], string> = {
  pinch: "h-5 w-5 border-2 border-warning bg-warning/30",
  palm: "h-10 w-10 border border-dashed border-info",
  fist: "h-8 w-8 border-2 border-info bg-info/20",
  point: "h-6 w-6 border-2 border-info",
  peace: "h-7 w-7 border-2 border-dotted border-warning",
  thumbs: "h-7 w-7 border-2 border-success",
  other: "h-6 w-6 border border-info/70",
};

/** A ring around a hand cursor that fills while a held gesture charges. */
function HoldRing({ progress }: { progress: number }) {
  const r = 16;
  const around = 2 * Math.PI * r;
  return (
    <svg aria-hidden viewBox="0 0 40 40" className="absolute left-1/2 top-1/2 h-10 w-10 -translate-x-1/2 -translate-y-1/2 -rotate-90">
      <circle cx="20" cy="20" r={r} fill="none" className="stroke-warning" strokeWidth="3" strokeDasharray={`${around * progress} ${around}`} />
    </svg>
  );
}

/** How long the indicator names what a gesture just did. */
export const FIRED_SHOW_MS = 1400;

/** The recognised gesture, its hold progress, and what the last one did. */
export function GestureIndicator({ tracking }: { tracking: Pick<HandTracking, "gesture" | "fired"> }) {
  const t = useT();
  const { gesture, fired } = tracking;
  const [flash, setFlash] = useState<string | null>(null);
  useEffect(() => {
    if (!fired) return;
    const key = fired.id === "swipe" ? (fired.step === -1 ? "swipe_prev" : "swipe_next") : fired.id;
    setFlash(t(`orb2.hands.fired.${key}`));
    const id = window.setTimeout(() => setFlash(null), FIRED_SHOW_MS);
    return () => window.clearTimeout(id);
    // `t` changes with the language only; the flash is keyed by the firing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fired?.seq]);
  const progress = gesture?.progress;
  return (
    <div
      role="status"
      aria-label={t("orb2.hands.indicator_label")}
      data-testid="orb-hands-indicator"
      data-gesture={gesture?.id ?? "none"}
      className="w-56 rounded-lg border border-border bg-background/85 px-2.5 py-1.5 text-xs text-foreground shadow-float backdrop-blur"
    >
      <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-muted-foreground">{t("orb2.hands.indicator_label")}</p>
      <p className={cn("mt-0.5 leading-4", gesture ? "text-foreground" : "text-muted-foreground")}>
        {t(gesture ? `orb2.hands.live.${gesture.id}` : "orb2.hands.live.none")}
      </p>
      {progress !== undefined && (
        <div className="mt-1 h-1 overflow-hidden rounded-full bg-muted" aria-hidden>
          <div className="h-full rounded-full bg-warning transition-[width] duration-75" style={{ width: `${Math.round(progress * 100)}%` }} />
        </div>
      )}
      {flash && (
        <p data-testid="orb-hands-fired" className="mt-1 font-medium leading-4 text-success">
          {flash}
        </p>
      )}
    </div>
  );
}

/** Hand cursors over the map, the gesture indicator, and a mirrored preview of the camera. */
export function HandOverlay({ tracking }: { tracking: HandTracking }) {
  const t = useT();
  const videoRef = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.srcObject = tracking.stream;
  }, [tracking.stream, tracking.status]);
  if (tracking.status !== "live") return null;
  return (
    <div className="pointer-events-none absolute inset-0 z-10" data-testid="orb-hands-overlay">
      {tracking.cursors.map((c) => (
        <span
          key={c.slot}
          data-pose={c.pose}
          className="absolute -translate-x-1/2 -translate-y-1/2"
          style={{ left: `${c.x * 100}%`, top: `${c.y * 100}%` }}
        >
          <span className={cn("block rounded-full shadow-float", POSE_RING[c.pose])} />
          {c.progress !== undefined && c.progress < 1 && <HoldRing progress={c.progress} />}
        </span>
      ))}
      <div className="absolute bottom-24 right-4 flex flex-col items-end gap-2">
        <GestureIndicator tracking={tracking} />
        <div className="relative overflow-hidden rounded-lg border border-border bg-background/60">
          <video ref={videoRef} autoPlay muted playsInline className="h-[90px] w-[120px] -scale-x-100 object-cover opacity-70" />
          <p className="absolute left-1.5 top-1 font-mono text-[9px] uppercase tracking-[0.2em] text-warning">
            {t("orb2.hands.preview")}
          </p>
        </div>
      </div>
    </div>
  );
}
