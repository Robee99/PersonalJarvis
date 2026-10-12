import { useId } from "react";
import type { VoiceState } from "@/store/events";
import { cn } from "@/lib/utils";

/**
 * The reactor ring: the alternative to Gigi in the centre of the deck
 * (lib/deckAvatar.ts).
 *
 * A housing ring, ten coil blocks, an inner ring and a white-hot core, all
 * vectors in the theme's primary colour. It moves only with the voice, like
 * the rest of the orb: the core grows and brightens with `--orb-level`
 * (`.deck-orb-reactor`, index.css) and the coils turn while a voice state is
 * live and stand still at rest (`.deck-reactor-coils`). Reduced motion stops
 * the turn.
 */
export function ReactorCore({
  size,
  voiceState,
  className,
}: {
  size: number;
  voiceState: VoiceState;
  className?: string;
}) {
  const coreId = useId();
  const C = 128;
  const coils = Array.from({ length: 10 }, (_, i) => coilPath(C, i * 36 - 12, i * 36 + 12, 80, 104));
  return (
    <div
      className={cn("pointer-events-none", className)}
      style={{ width: size, height: size }}
      data-testid="deck-reactor"
    >
      <div className="deck-orb-reactor absolute inset-0">
        <svg viewBox="0 0 256 256" className="h-full w-full" aria-hidden>
          <defs>
            <radialGradient id={coreId}>
              <stop offset="0%" stopColor="white" stopOpacity={1} />
              <stop offset="45%" stopColor="hsl(var(--primary))" stopOpacity={0.95} />
              <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
            </radialGradient>
          </defs>
          <circle cx={C} cy={C} r={118} fill="none" stroke="hsl(var(--primary))" strokeWidth={2} opacity={0.35} />
          <circle cx={C} cy={C} r={110} fill="none" stroke="hsl(var(--primary))" strokeWidth={1} opacity={0.6} />
          <g className="deck-reactor-coils" data-voice={voiceState}>
            {coils.map((d, i) => (
              <path
                key={i}
                d={d}
                fill="hsl(var(--primary))"
                fillOpacity={0.35}
                stroke="hsl(var(--primary))"
                strokeWidth={1.5}
              />
            ))}
          </g>
          <circle cx={C} cy={C} r={72} fill="none" stroke="hsl(var(--primary))" strokeWidth={3} opacity={0.85} />
          <circle cx={C} cy={C} r={64} fill={`url(#${coreId})`} />
          <circle cx={C} cy={C} r={26} fill="white" opacity={0.9} />
        </svg>
      </div>
    </div>
  );
}

/** One coil block: the ring segment between two radii and two angles. */
function coilPath(c: number, a0: number, a1: number, r0: number, r1: number): string {
  const at = (deg: number, r: number) => {
    const rad = ((deg - 90) * Math.PI) / 180;
    return `${(c + r * Math.cos(rad)).toFixed(2)} ${(c + r * Math.sin(rad)).toFixed(2)}`;
  };
  return [
    `M ${at(a0, r1)}`,
    `A ${r1} ${r1} 0 0 1 ${at(a1, r1)}`,
    `L ${at(a1, r0)}`,
    `A ${r0} ${r0} 0 0 0 ${at(a0, r0)}`,
    "Z",
  ].join(" ");
}
