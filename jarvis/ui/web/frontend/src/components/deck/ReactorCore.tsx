import { useId } from "react";
import type { VoiceState } from "@/store/events";
import { cn } from "@/lib/utils";

/**
 * The reactor ring: the alternative to Gigi in the centre of the deck
 * (lib/deckAvatar.ts).
 *
 * A steel housing ring with ten coil blocks, a ring of twelve light cells,
 * and a triangular core that points down, glowing white at its centre — all
 * vectors, lit in the theme's primary colour. It moves only with the voice, like
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
  const uid = useId();
  const C = 128;
  const coils = Array.from({ length: 10 }, (_, i) => coilPath(C, i * 36 - 11, i * 36 + 11, 88, 112));
  const cells = Array.from({ length: 12 }, (_, i) => coilPath(C, i * 30 - 11, i * 30 + 11, 66, 80));
  // The core triangle, apex down: three points on a circle of radius r.
  const tri = (r: number) =>
    [90, 210, 330]
      .map((deg) => {
        const rad = (deg * Math.PI) / 180;
        return `${(C + r * Math.cos(rad)).toFixed(2)},${(C + r * Math.sin(rad)).toFixed(2)}`;
      })
      .join(" ");
  return (
    <div
      className={cn("pointer-events-none", className)}
      style={{ width: size, height: size }}
      data-testid="deck-reactor"
    >
      <div className="deck-orb-reactor absolute inset-0">
        <svg viewBox="0 0 256 256" className="h-full w-full" aria-hidden>
          <defs>
            <radialGradient id={`${uid}-core`}>
              <stop offset="0%" stopColor="white" stopOpacity={1} />
              <stop offset="40%" stopColor="hsl(var(--primary))" stopOpacity={0.95} />
              <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
            </radialGradient>
            <linearGradient id={`${uid}-steel`} x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor="#e8eef2" />
              <stop offset="45%" stopColor="#8a96a0" />
              <stop offset="70%" stopColor="#c9d3da" />
              <stop offset="100%" stopColor="#4b555d" />
            </linearGradient>
          </defs>
          {/* The housing: a dark steel band between two bright rims. */}
          <circle cx={C} cy={C} r={121} fill="none" stroke="hsl(var(--primary))" strokeWidth={1.5} opacity={0.5} />
          <circle cx={C} cy={C} r={100} fill="none" stroke="#1a232a" strokeWidth={26} />
          <circle cx={C} cy={C} r={113} fill="none" stroke={`url(#${uid}-steel)`} strokeWidth={2} opacity={0.8} />
          <circle cx={C} cy={C} r={87} fill="none" stroke={`url(#${uid}-steel)`} strokeWidth={1.5} opacity={0.7} />
          {/* Ten armoured coil blocks; they turn while a voice state is live. */}
          <g className="deck-reactor-coils" data-voice={voiceState}>
            {coils.map((d, i) => (
              <path key={i} d={d} fill={`url(#${uid}-steel)`} stroke="#0b1116" strokeWidth={1.2} />
            ))}
          </g>
          {/* The light cells between the housing and the core. */}
          {cells.map((d, i) => (
            <path
              key={`c${i}`}
              d={d}
              fill="hsl(var(--primary))"
              fillOpacity={0.55}
              stroke="hsl(var(--primary))"
              strokeWidth={0.8}
              style={{ filter: "drop-shadow(0 0 4px hsl(var(--primary)))" }}
            />
          ))}
          <circle cx={C} cy={C} r={62} fill={`url(#${uid}-core)`} opacity={0.55} />
          {/* The triangle: a steel frame round a lit inner triangle. */}
          <polygon points={tri(60)} fill="none" stroke={`url(#${uid}-steel)`} strokeWidth={7} strokeLinejoin="round" />
          <polygon points={tri(48)} fill="none" stroke="hsl(var(--primary))" strokeWidth={3} strokeLinejoin="round" opacity={0.9} />
          <polygon points={tri(36)} fill={`url(#${uid}-core)`} stroke="white" strokeWidth={1} strokeOpacity={0.6} />
          <circle cx={C} cy={C} r={12} fill="white" opacity={0.95} />
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
