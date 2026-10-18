import { useId, type ReactNode } from "react";
import type { VoiceState } from "@/store/events";
import { fmtK } from "@/components/stark/ContextMeter";

/**
 * The arc reactor's halo: the rings of the owner's concept board drawn round
 * the orb (DeckOrb stays the centre, with its own voice-driven core and step
 * arcs). Outer to inner: a hairline, a turning tick dial, the name on a
 * circular track, three counter-turning armour segments, the context arc
 * (real: the brain's context fill against its window, from the swapper) and
 * a dashed inner race.
 *
 * The rings turn faster while the assistant listens, thinks or speaks
 * (`.stark-halo[data-voice]`, index.css) and stand still under reduced
 * motion. The halo is a picture, `aria-hidden`; the orb in the middle keeps
 * its button.
 */
export function ArcHalo({
  size,
  voiceState,
  busy,
  context,
  title,
  children,
}: {
  size: number;
  voiceState: VoiceState;
  busy: boolean;
  /** The context fill, or null when no local brain reports one. */
  context: { used: number; window: number } | null;
  /** The name on the circular track. */
  title: string;
  children: ReactNode;
}) {
  const textPathId = useId();
  const C = 500;
  const point = (deg: number, r: number): [number, number] => {
    const a = ((deg - 90) * Math.PI) / 180;
    return [C + r * Math.cos(a), C + r * Math.sin(a)];
  };
  const arc = (a0: number, a1: number, r: number) => {
    const [x0, y0] = point(a0, r);
    const [x1, y1] = point(a1, r);
    return `M ${x0} ${y0} A ${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1} ${y1}`;
  };
  const ticks = Array.from({ length: 120 }, (_, i) => i * 3);
  const ctxFill = context && context.window > 0 ? Math.min(1, context.used / context.window) : 0;
  const ctxHot = ctxFill >= 0.85;
  // The context arc runs 270° from the lower left, like a speedometer.
  const CTX_START = -135;
  const CTX_SWEEP = 270;
  const caption = `${title} · ${title} · ${title} · `;

  return (
    <div
      className="stark-halo relative flex items-center justify-center"
      style={{ width: size, height: size }}
      data-voice={voiceState}
      data-busy={busy ? "true" : "false"}
      data-testid="stark-halo"
    >
      <svg viewBox="0 0 1000 1000" className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden>
        <defs>
          <path id={textPathId} d={`M ${C} ${C - 452} a 452 452 0 1 1 -0.1 0`} />
          <radialGradient id={`${textPathId}-wash`}>
            <stop offset="55%" stopColor="hsl(var(--primary))" stopOpacity={0} />
            <stop offset="78%" stopColor="hsl(var(--primary))" stopOpacity={0.07} />
            <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
          </radialGradient>
        </defs>
        <circle cx={C} cy={C} r={496} fill={`url(#${textPathId}-wash)`} />
        <circle cx={C} cy={C} r={494} fill="none" stroke="hsl(var(--primary))" strokeWidth={1.2} opacity={0.3} />

        {/* The tick dial: every 3°, a long tick every 30°. */}
        <g className="stark-spin stark-spin-dial">
          {ticks.map((deg) => {
            const long = deg % 30 === 0;
            const [ax, ay] = point(deg, 486);
            const [bx, by] = point(deg, long ? 462 : 474);
            return (
              <line
                key={deg}
                x1={ax}
                y1={ay}
                x2={bx}
                y2={by}
                stroke="hsl(var(--primary))"
                strokeWidth={long ? 3 : 1.4}
                opacity={long ? 0.85 : 0.4}
              />
            );
          })}
        </g>

        {/* The name, running round the dial. */}
        <g className="stark-spin stark-spin-text">
          <text className="fill-primary font-mono" style={{ fontSize: 17, letterSpacing: 7 }} opacity={0.75}>
            <textPath href={`#${textPathId}`}>{caption}</textPath>
          </text>
        </g>

        {/* Armour: three heavy segments, turning against the dial. */}
        <g className="stark-spin stark-spin-armour">
          {[0, 120, 240].map((a) => (
            <path
              key={a}
              d={arc(a + 8, a + 98, 418)}
              fill="none"
              stroke="hsl(var(--primary))"
              strokeWidth={12}
              opacity={0.55}
              strokeLinecap="butt"
            />
          ))}
          {[0, 120, 240].map((a) => (
            <path key={`t${a}`} d={arc(a + 102, a + 112, 418)} fill="none" stroke="hsl(var(--primary))" strokeWidth={12} opacity={0.18} />
          ))}
        </g>

        {/* The context arc: a track and the fill, with its reading at the foot. */}
        <path d={arc(CTX_START, CTX_START + CTX_SWEEP, 392)} fill="none" stroke="hsl(var(--primary))" strokeWidth={5} opacity={0.14} />
        {ctxFill > 0.002 && (
          <path
            d={arc(CTX_START, CTX_START + CTX_SWEEP * ctxFill, 392)}
            fill="none"
            stroke={ctxHot ? "hsl(var(--stark-hot))" : "hsl(var(--primary))"}
            strokeWidth={5}
            style={{ filter: `drop-shadow(0 0 6px ${ctxHot ? "hsl(var(--stark-hot))" : "hsl(var(--primary))"})` }}
            data-testid="stark-halo-context"
          />
        )}
        {context && (
          <text
            x={C}
            y={C + 400}
            textAnchor="middle"
            className="fill-muted-foreground font-mono"
            style={{ fontSize: 18, letterSpacing: 3 }}
          >
            {`CTX ${fmtK(context.used)} / ${fmtK(context.window)}`}
          </text>
        )}

        {/* The inner race. */}
        <circle
          cx={C}
          cy={C}
          r={368}
          fill="none"
          stroke="hsl(var(--primary))"
          strokeWidth={2}
          strokeDasharray="2 10"
          opacity={0.5}
          className="stark-spin stark-spin-race"
        />
        {[0, 90, 180, 270].map((deg) => {
          const [x, y] = point(deg, 352);
          return (
            <path
              key={deg}
              d={`M ${x} ${y} l -7 -12 h 14 z`}
              transform={`rotate(${deg + 180} ${x} ${y})`}
              fill="hsl(var(--primary))"
              opacity={0.8}
            />
          );
        })}
      </svg>
      <div className="relative">{children}</div>
    </div>
  );
}
