import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";
import { fmtK, type DeckContextSettings } from "@/components/stark/ContextMeter";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

export interface ContextChangeResult {
  ok: boolean;
  window: number | null;
  vision_on_gpu?: boolean;
  seconds?: number | null;
  error?: string | null;
  hermes?: { changed: string[]; error?: string } | null;
}

/** Restart the local brain with `ctx` tokens; `null` reloads it as it is. */
export async function changeContext(ctx: number | null): Promise<ContextChangeResult> {
  const res = await fetch("/api/deck/context", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ctx }),
  });
  const body = (await res.json().catch(() => ({}))) as Partial<ContextChangeResult> & { detail?: string };
  if (!res.ok && body.window == null) throw new Error(body.detail || body.error || `HTTP ${res.status}`);
  return { ok: Boolean(body.ok), window: body.window ?? null, ...body };
}

/**
 * The context slider and the reload button under the context bar. Moving the
 * slider only picks a size; "Apply" restarts the model with it (the model
 * server waits for a running turn, and keeps the old size if the new one does
 * not load). Reload restarts the model as it is.
 */
export function ContextControls({ settings }: { settings: DeckContextSettings }) {
  const t = useT();
  const qc = useQueryClient();
  const choices = settings.choices;
  const loaded = settings.loaded ?? choices[choices.length - 1] ?? 0;
  const [picked, setPicked] = useState(loaded);
  const [note, setNote] = useState<{ text: string; bad: boolean } | null>(null);
  useEffect(() => setPicked(loaded), [loaded]);

  const run = useMutation({
    mutationFn: changeContext,
    onSuccess: (r, ctx) => {
      if (r.ok) {
        setNote({
          text: t(ctx === null ? "stark.context.reloaded" : "stark.context.changed").replace("{size}", fmtK(r.window ?? 0)),
          bad: false,
        });
      } else {
        setNote({ text: r.error || t("stark.context.change_failed"), bad: true });
      }
      if (r.hermes?.error) setNote({ text: r.hermes.error, bad: true });
    },
    onError: (err: Error) => setNote({ text: err.message, bad: true }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["deck", "context"] }),
  });

  if (choices.length < 2) return null;
  const index = Math.max(0, choices.indexOf(picked));
  const busy = run.isPending;
  const hint =
    settings.vision_gpu_max && picked <= settings.vision_gpu_max
      ? t("stark.context.hint_fast")
      : picked > 65_536
        ? t("stark.context.hint_big")
        : t("stark.context.hint_slow_vision");

  return (
    <div className="mt-2 flex flex-col gap-1" data-testid="stark-context-controls">
      <div className="flex items-center gap-2">
        <input
          type="range"
          min={0}
          max={choices.length - 1}
          step={1}
          value={index}
          disabled={busy}
          onChange={(e) => {
            setPicked(choices[Number(e.target.value)]);
            setNote(null);
          }}
          className="h-1 flex-1 cursor-pointer accent-[hsl(var(--primary))] disabled:cursor-wait"
          aria-label={t("stark.context.slider")}
          aria-valuetext={fmtK(picked)}
          data-testid="stark-context-slider"
        />
        <span className="w-10 text-right font-mono text-xs tabular-nums text-foreground" data-testid="stark-context-picked">
          {fmtK(picked)}
        </span>
        <button
          type="button"
          disabled={busy || picked === loaded}
          onClick={() => run.mutate(picked)}
          className="rounded border border-primary/40 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-primary hover:bg-primary/10 disabled:opacity-40"
          data-testid="stark-context-apply"
        >
          {t("stark.context.apply")}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => run.mutate(null)}
          className="rounded border border-primary/40 p-1 text-primary hover:bg-primary/10 disabled:opacity-40"
          title={t("stark.context.reload")}
          aria-label={t("stark.context.reload")}
          data-testid="stark-context-reload"
        >
          <RotateCw className={cn("h-3 w-3", busy && "animate-spin")} aria-hidden />
        </button>
      </div>
      <p
        className={cn("text-[10px]", note?.bad ? "text-[hsl(var(--stark-hot))]" : "text-muted-foreground")}
        data-testid="stark-context-note"
        aria-live="polite"
      >
        {busy ? t("stark.context.restarting") : note ? note.text : hint}
      </p>
    </div>
  );
}
