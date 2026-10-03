import { useCallback, useEffect, useState } from "react";
import { Hand, Loader2, X } from "lucide-react";

import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";

/**
 * "Hands view": the HOLO hand-gesture deck (jarvis/ui/web/holo_routes.py)
 * over the whole window, with the memory vault as orbs you grab.
 *
 * The deck runs in a same-origin frame rather than the system browser so its
 * vault requests carry this window's session; a browser opened elsewhere has
 * none. The first open downloads the pinned hand-tracking files once
 * (POST /api/holo/install); the camera itself stays inside the frame.
 */
export function HoloDeckButton(): JSX.Element {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [phase, setPhase] = useState<"idle" | "preparing" | "open">("idle");

  const open = useCallback(async () => {
    setPhase("preparing");
    try {
      const res = await fetch("/api/holo/install", { method: "POST" });
      const body = (await res.json().catch(() => ({}))) as { error?: string };
      if (!res.ok) throw new Error(body.error ?? `HTTP ${res.status}`);
      setPhase("open");
    } catch (error) {
      setPhase("idle");
      pushToast(
        "error",
        `${t("wiki_holo.install_failed")}: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }, [pushToast, t]);

  useEffect(() => {
    if (phase !== "open") return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPhase("idle");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase]);

  return (
    <>
      <button
        type="button"
        className="ml-auto my-1.5 inline-flex items-center gap-1.5 self-center rounded-md px-2.5 py-1.5 text-body text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong disabled:opacity-60"
        onClick={() => void open()}
        disabled={phase === "preparing"}
        title={t("wiki_holo.open_title")}
        data-testid="wiki-holo-open"
      >
        {phase === "preparing" ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        ) : (
          <Hand className="h-3.5 w-3.5" aria-hidden />
        )}
        <span>{t(phase === "preparing" ? "wiki_holo.preparing" : "wiki_holo.open")}</span>
      </button>
      {phase === "open" && (
        <div className="fixed inset-0 z-[110] bg-black" data-testid="wiki-holo-overlay">
          <iframe
            title={t("wiki_holo.frame_title")}
            src="/holo/"
            allow="camera; fullscreen"
            className="h-full w-full border-0"
          />
          <button
            type="button"
            className="absolute right-3 top-3 inline-flex items-center gap-1.5 rounded-md bg-black/60 px-2.5 py-1.5 text-body text-white hover:bg-black/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
            onClick={() => setPhase("idle")}
            data-testid="wiki-holo-close"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
            <span>{t("wiki_holo.close")}</span>
          </button>
        </div>
      )}
    </>
  );
}
