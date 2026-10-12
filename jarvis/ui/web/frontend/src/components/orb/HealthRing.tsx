/**
 * A project's health at a glance: a thin ring in the health colour whose arc
 * is the measured progress (done tasks and blockers over all of them). No
 * arc means nothing is tracked, which is said, never drawn as 0 %.
 */
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { HEALTH_HEX, type Health } from "./brainMap";

export function HealthRing({ health, size = 34, className }: { health: Health; size?: number; className?: string }) {
  const t = useT();
  const colour = HEALTH_HEX[health.colour];
  const stroke = Math.max(2, size / 14);
  const r = size / 2 - stroke;
  const c = 2 * Math.PI * r;
  const progress = health.progress;
  const pct = progress === null ? null : Math.round(progress * 100);
  const label = `${t(`orb2.health.colour.${health.colour}`)} · ${
    pct === null ? t("orb2.health.untracked") : `${pct}%`
  }`;
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      data-health={health.colour}
      className={cn("relative inline-flex shrink-0 items-center justify-center", className)}
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={colour} strokeOpacity={0.18} strokeWidth={stroke} />
        {progress !== null && (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke={colour}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={`${c * progress} ${c}`}
            style={{ filter: `drop-shadow(0 0 3px ${colour})` }}
          />
        )}
      </svg>
      <span className="absolute font-mono text-[9px] font-semibold" style={{ color: colour }}>
        {pct === null ? "—" : pct}
      </span>
    </span>
  );
}
