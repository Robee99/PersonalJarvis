import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * One panel of the Stark HUD (HUD v4): a dark glass plate with a cyan
 * hairline, bright corner brackets (index.css, `.stark-panel`) and a numbered
 * title strip, the way the owner's concept boards draw them ("01 // SYSTEM
 * STATUS"). The panel is the frame only; what it shows is its children.
 */
export function StarkPanel({
  index,
  title,
  meta,
  action,
  className,
  bodyClassName,
  testId,
  children,
}: {
  /** The two-digit number in front of the title. */
  index?: string;
  title: string;
  /** A short figure or word at the right of the strip. */
  meta?: ReactNode;
  /** A control at the far right of the strip. */
  action?: ReactNode;
  className?: string;
  bodyClassName?: string;
  testId?: string;
  children: ReactNode;
}) {
  return (
    <section data-testid={testId} className={cn("stark-panel flex min-h-0 flex-col", className)}>
      <header className="flex items-center gap-2 px-3 pb-1.5 pt-2">
        {index && <span className="font-mono text-[10px] tabular-nums text-primary/60">{index}</span>}
        <h2 className="truncate font-mono text-[11px] font-semibold uppercase tracking-[0.24em] text-primary">
          {title}
        </h2>
        <span aria-hidden className="stark-rule h-px min-w-4 flex-1" />
        {meta !== undefined && (
          <span className="shrink-0 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            {meta}
          </span>
        )}
        {action}
      </header>
      <div className={cn("relative min-h-0 flex-1 px-3 pb-3", bodyClassName)}>{children}</div>
    </section>
  );
}

/**
 * A ring gauge: a tick bezel, a track, the value as a glowing arc that turns
 * reactor-orange past `hotAt`, and the reading in the middle.
 */
export function RingGauge({
  value,
  label,
  readout,
  sub,
  size = 88,
  hotAt = 0.85,
  testId,
}: {
  /** 0..1; null when the sensor did not answer (drawn empty, read "—"). */
  value: number | null;
  label: string;
  readout: string;
  sub?: string;
  size?: number;
  hotAt?: number;
  testId?: string;
}) {
  const v = value === null ? 0 : Math.max(0, Math.min(1, value));
  const r = 38;
  const c = 2 * Math.PI * r;
  const hot = value !== null && v >= hotAt;
  const color = hot ? "hsl(var(--stark-hot))" : "hsl(var(--primary))";
  return (
    <div className="flex flex-col items-center" data-testid={testId} data-value={value === null ? "" : v.toFixed(3)}>
      <svg width={size} height={size} viewBox="0 0 100 100" aria-hidden className="overflow-visible">
        <circle cx={50} cy={50} r={47} fill="none" stroke="hsl(var(--primary))" strokeWidth={2.4} strokeDasharray="0.6 3.3" opacity={0.45} />
        <circle cx={50} cy={50} r={r} fill="none" stroke="hsl(var(--primary))" strokeWidth={7} opacity={0.12} />
        <circle
          cx={50}
          cy={50}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={7}
          strokeDasharray={`${c * v} ${c}`}
          transform="rotate(-90 50 50)"
          className="stark-gauge-arc"
          style={{ filter: `drop-shadow(0 0 4px ${color})` }}
        />
        <circle cx={50} cy={50} r={30} fill="none" stroke="hsl(var(--primary))" strokeWidth={0.6} opacity={0.5} />
        <text x={50} y={sub ? 50 : 55} textAnchor="middle" className="fill-foreground font-mono" style={{ fontSize: 17 }}>
          {readout}
        </text>
        {sub && (
          <text x={50} y={64} textAnchor="middle" className="fill-muted-foreground font-mono" style={{ fontSize: 8.5 }}>
            {sub}
          </text>
        )}
      </svg>
      <span className="mt-1 font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground">{label}</span>
    </div>
  );
}

/** A status lamp: lit cyan, warning orange, or dark. */
export function StarkLamp({ state }: { state: "on" | "warn" | "off" }) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block h-2 w-2 shrink-0 rotate-45",
        state === "on" && "bg-primary shadow-[0_0_8px_hsl(var(--primary))]",
        state === "warn" && "bg-[hsl(var(--stark-hot))] shadow-[0_0_8px_hsl(var(--stark-hot))]",
        state === "off" && "border border-muted-foreground/60",
      )}
    />
  );
}
