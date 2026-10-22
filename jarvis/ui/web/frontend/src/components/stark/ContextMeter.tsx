import { useQuery } from "@tanstack/react-query";
import { StarkPanel } from "@/components/stark/StarkPanel";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/** What the context slider offers: the loaded window and the sizes it may pick. */
export interface DeckContextSettings {
  loaded: number | null;
  choices: number[];
  vision_on_gpu: boolean;
  vision_gpu_max?: number | null;
  busy?: boolean;
}

export interface DeckContext {
  available: boolean;
  settings?: DeckContextSettings;
  used?: number;
  window?: number;
  model?: string;
  at?: number | null;
  compress_at?: number | null;
}

async function fetchContext(): Promise<DeckContext> {
  const res = await fetch("/api/deck/context");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as DeckContext;
}

/** The meter's scale: 128k, or the model's window when that is larger. */
export const METER_MAX = 131_072;
const SEGMENTS = 48;

export function fmtK(tokens: number): string {
  if (tokens < 1000) return String(tokens);
  const k = tokens / 1024;
  return `${k >= 100 ? Math.round(k) : k.toFixed(1).replace(/\.0$/, "")}k`;
}

/**
 * How full the local brain's context was on its newest agent turn
 * (`/api/deck/context`, which reads llama-server's own count through the
 * model swapper). The bar runs to 128k; the model's real window is the red
 * line, and Hermes's trim point a tick before it, so a full window is never a
 * surprise: past the tick Hermes compresses the conversation, at the line the
 * model would refuse.
 */
/** The brain's context fill, polled every four seconds; one query for every reader. */
export function useDeckContext() {
  return useQuery<DeckContext>({
    queryKey: ["deck", "context"],
    queryFn: fetchContext,
    refetchInterval: 4_000,
    retry: false,
  });
}

export function ContextMeter({ className }: { className?: string }) {
  const t = useT();
  const q = useDeckContext();
  const ctx = q.data;
  const ready = Boolean(ctx?.available && typeof ctx.used === "number" && ctx.window);
  const used = ready ? (ctx!.used as number) : 0;
  const window = ready ? (ctx!.window as number) : 0;
  const max = Math.max(METER_MAX, window);
  const fill = used / max;
  const windowAt = window / max;
  const compressAt = ready && ctx!.compress_at ? ctx!.compress_at / max : null;
  const ofWindow = window ? used / window : 0;
  const tone = ofWindow >= 0.85 ? "hot" : ofWindow >= 0.6 ? "warm" : "cool";

  return (
    <StarkPanel
      index="03"
      title={t("stark.context.title")}
      meta={ready ? ctx!.model : undefined}
      className={className}
      testId="stark-context"
    >
      {!ready ? (
        <p className="text-xs text-muted-foreground" data-testid="stark-context-empty">
          {t(q.isError ? "stark.context.error" : "stark.context.none")}
        </p>
      ) : (
        <div className="flex flex-col gap-2" data-testid="stark-context-meter" data-used={used} data-window={window}>
          <div className="flex items-baseline gap-2">
            <span
              className={cn(
                "font-mono text-2xl tabular-nums",
                tone === "hot" ? "text-[hsl(var(--stark-hot))]" : "text-foreground",
              )}
            >
              {fmtK(used)}
            </span>
            <span className="font-mono text-xs text-muted-foreground">
              / {fmtK(window)} {t("stark.context.window")}
            </span>
            <span className="ml-auto font-mono text-xs tabular-nums text-primary">
              {Math.round(ofWindow * 100)}%
            </span>
          </div>
          <div className="relative pt-3">
            <div className="flex h-3 gap-[2px]" aria-hidden>
              {Array.from({ length: SEGMENTS }, (_, i) => {
                const at = (i + 0.5) / SEGMENTS;
                const lit = at <= fill;
                const beyond = at > windowAt;
                return (
                  <span
                    key={i}
                    className={cn(
                      "flex-1",
                      beyond
                        ? "stark-seg-beyond"
                        : lit
                          ? tone === "hot"
                            ? "bg-[hsl(var(--stark-hot))] shadow-[0_0_6px_hsl(var(--stark-hot))]"
                            : "bg-primary shadow-[0_0_6px_hsl(var(--primary))]"
                          : "bg-primary/15",
                    )}
                  />
                );
              })}
            </div>
            {compressAt !== null && (
              <Mark
                at={compressAt}
                label={`${t("stark.context.trim")} ${fmtK(ctx!.compress_at as number)}`}
                below
              />
            )}
            <Mark at={windowAt} label={`${t("stark.context.limit")} ${fmtK(window)}`} hot />
            <div className="mt-2.5 flex justify-between font-mono text-[9px] text-muted-foreground">
              <span>0</span>
              <span>{fmtK(max)}</span>
            </div>
          </div>
        </div>
      )}
    </StarkPanel>
  );
}

/** A mark on the bar: its label above it (the limit) or below it (the trim),
 * so two marks a few percent apart never print over each other. */
function Mark({ at, label, hot, below }: { at: number; label: string; hot?: boolean; below?: boolean }) {
  return (
    <span
      className={cn(
        "pointer-events-none absolute flex -translate-x-1/2 items-center",
        below ? "top-3 flex-col-reverse" : "top-0 flex-col",
      )}
      style={{ left: `${Math.min(100, at * 100)}%` }}
    >
      <span
        className={cn(
          "whitespace-nowrap font-mono text-[8px] uppercase leading-none tracking-wider",
          hot ? "text-[hsl(var(--stark-hot))]" : "text-muted-foreground",
        )}
      >
        {label}
      </span>
      <span
        className={cn(
          "w-px",
          below ? "mb-0.5 h-4" : "mt-0.5 h-4",
          hot ? "bg-[hsl(var(--stark-hot))]" : "bg-foreground/60",
        )}
      />
    </span>
  );
}
