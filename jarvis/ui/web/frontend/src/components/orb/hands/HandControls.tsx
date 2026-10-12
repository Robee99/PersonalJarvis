/**
 * The Orb's hand-control toggle, its status, and the hand cursors.
 *
 * Off by default: the camera only starts when the person turns hands on, and
 * goes off with the toggle or the view. Whatever the state, the mouse,
 * keyboard and touch keep working; the panel says so, and says exactly why
 * hands are not available when they are not.
 */
import { useEffect, useRef } from "react";
import { Hand, Loader2 } from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { HAND_PROBLEMS, type HandTracking } from "./useHandTracking";

const BUSY = new Set(["checking", "installing", "loading", "camera"]);

export function HandToggle({ tracking, className }: { tracking: HandTracking; className?: string }) {
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
          className="pointer-events-auto absolute right-0 top-11 z-20 w-72 rounded-xl border border-cyan-400/20 bg-black/80 p-3 text-xs text-slate-300 shadow-[0_0_24px_rgba(79,209,232,0.12)] backdrop-blur"
        >
          <p className={cn("font-mono text-[10px] uppercase tracking-[0.2em]", problem ? "text-rose-300" : "text-cyan-300/90")}>
            {t(`orb2.hands.status.${status}`)}
          </p>
          {problem && <p className="mt-1.5 leading-5">{t(`orb2.hands.why.${status}`)}</p>}
          {detail && problem && <p className="mt-1 break-words font-mono text-[10px] text-slate-500">{detail}</p>}
          {status === "live" && (
            <ul className="mt-1.5 space-y-0.5 leading-5" data-testid="orb-hands-gestures">
              <li>{t("orb2.hands.gesture_point")}</li>
              <li>{t("orb2.hands.gesture_pinch")}</li>
              <li>{t("orb2.hands.gesture_palm")}</li>
              <li>{t("orb2.hands.gesture_zoom")}</li>
            </ul>
          )}
          <div className="mt-2 flex flex-wrap gap-1.5">
            {status === "files-missing" && (
              <button
                type="button"
                data-testid="orb-hands-install"
                onClick={() => tracking.start({ install: true })}
                className="rounded-full bg-cyan-400/15 px-2.5 py-1 text-cyan-100 hover:bg-cyan-400/25"
              >
                {t("orb2.hands.install")}
              </button>
            )}
            {problem && status !== "files-missing" && status !== "unsupported" && (
              <button
                type="button"
                data-testid="orb-hands-retry"
                onClick={() => tracking.start({ install: status === "install-failed" })}
                className="rounded-full bg-cyan-400/15 px-2.5 py-1 text-cyan-100 hover:bg-cyan-400/25"
              >
                {t("orb2.hands.retry")}
              </button>
            )}
            <button
              type="button"
              onClick={() => tracking.stop()}
              className="rounded-full border border-white/10 px-2.5 py-1 text-slate-300 hover:border-white/25"
            >
              {t(problem ? "orb2.hands.use_mouse" : "orb2.hands.turn_off")}
            </button>
          </div>
          <p className="mt-2 text-[10px] leading-4 text-slate-500">
            {t(problem ? "orb2.hands.fallback" : "orb2.hands.privacy")}
          </p>
        </div>
      )}
    </div>
  );
}

const POSE_RING: Record<string, string> = {
  pinch: "h-5 w-5 border-2 border-amber-300 bg-amber-300/30 shadow-[0_0_16px_rgba(255,193,69,0.7)]",
  palm: "h-10 w-10 border border-dashed border-cyan-200/80 shadow-[0_0_20px_rgba(79,209,232,0.35)]",
  point: "h-6 w-6 border-2 border-cyan-200 shadow-[0_0_14px_rgba(79,209,232,0.6)]",
  other: "h-6 w-6 border border-cyan-200/60",
};

/** Hand cursors over the map, and a small mirrored preview of the camera. */
export function HandOverlay({ tracking }: { tracking: HandTracking }) {
  const t = useT();
  const videoRef = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.srcObject = tracking.stream;
  }, [tracking.stream]);
  if (tracking.status !== "live") return null;
  return (
    <div className="pointer-events-none absolute inset-0 z-10" data-testid="orb-hands-overlay">
      {tracking.cursors.map((c) => (
        <span
          key={c.slot}
          data-pose={c.pose}
          className={cn("absolute -translate-x-1/2 -translate-y-1/2 rounded-full", POSE_RING[c.pose])}
          style={{ left: `${c.x * 100}%`, top: `${c.y * 100}%` }}
        />
      ))}
      <div className="absolute bottom-24 right-4 overflow-hidden rounded-lg border border-amber-300/30 bg-black/60">
        <video ref={videoRef} autoPlay muted playsInline className="h-[90px] w-[120px] -scale-x-100 object-cover opacity-70" />
        <p className="absolute left-1.5 top-1 font-mono text-[9px] uppercase tracking-[0.2em] text-amber-200/90">
          {t("orb2.hands.preview")}
        </p>
      </div>
    </div>
  );
}
