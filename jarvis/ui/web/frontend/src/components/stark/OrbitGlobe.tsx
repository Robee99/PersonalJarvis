import { useEffect, useRef, useState } from "react";
import { releaseWebglContext, useWebglSurface } from "@/hooks/useWebglSurface";
import { cn } from "@/lib/utils";

/**
 * The HUD's dotted Earth (cobe, WebGL) with points raised above it as
 * markers: satellites on the Orbital screen, events and satellites on the
 * World screen.
 *
 * Markers are pushed into the running globe on every change, so a live
 * position moves without rebuilding the WebGL scene. The globe turns slowly
 * unless `spin` is off or the person prefers reduced motion.
 *
 * With `interactive`, the globe can be turned by dragging or with the arrow
 * keys, zoomed with `scale`, and sent to a point with `target` (it eases
 * there). A lost WebGL context is handed back and the scene rebuilt
 * (`useWebglSurface`).
 */
export interface OrbitMarker {
  lat: number;
  lon: number;
  size: number;
  kind: "iss" | "station" | "track" | "home" | "quake" | "quake_high" | "event";
}

const COLOURS: Record<OrbitMarker["kind"], [number, number, number]> = {
  iss: [1, 0.55, 0.15],
  station: [0.35, 0.85, 1],
  track: [0.25, 0.65, 0.95],
  home: [0.3, 1, 0.6],
  quake: [1, 0.72, 0.25],
  quake_high: [1, 0.3, 0.25],
  event: [1, 0.85, 0.35],
};

/** A point to turn the globe to; a new `seq` turns it again to the same point. */
export interface GlobeTarget {
  lat: number;
  lon: number;
  seq: number;
}

type Globe = { update: (s: Record<string, unknown>) => void; destroy: () => void };

/** The globe's turn (phi) and tilt (theta) that put a point in the middle. */
export function anglesFor(lat: number, lon: number): { phi: number; theta: number } {
  return {
    phi: (3 * Math.PI) / 2 - (lon * Math.PI) / 180,
    theta: Math.max(-1, Math.min(1, (lat * Math.PI) / 180)),
  };
}

/** The shortest way round from one turn to another, in (-π, π]. */
function shortest(from: number, to: number): number {
  const d = (to - from) % (2 * Math.PI);
  return d > Math.PI ? d - 2 * Math.PI : d <= -Math.PI ? d + 2 * Math.PI : d;
}

export function OrbitGlobe({
  markers,
  facing,
  spin = true,
  scale = 1,
  target = null,
  interactive = false,
  elevation = 0.06,
  label,
  className,
}: {
  markers: OrbitMarker[];
  /** Where to face first: the ISS, or the owner's city. */
  facing: { lat: number; lon: number } | null;
  spin?: boolean;
  /** 1 fills the canvas; larger zooms in. */
  scale?: number;
  target?: GlobeTarget | null;
  interactive?: boolean;
  /** How high markers stand off the surface (satellites high, ground events low). */
  elevation?: number;
  /** What a screen reader hears for an interactive globe. */
  label?: string;
  className?: string;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const globeRef = useRef<Globe | null>(null);
  const { generation } = useWebglSurface(hostRef);
  const [failed, setFailed] = useState(false);
  const live = useRef({ markers, spin, scale, target });
  live.current = { markers, spin, scale, target };
  // The view itself lives outside React: dragging must not re-render.
  const view = useRef({ phi: 0, theta: 0.25, goal: null as { phi: number; theta: number } | null, seq: -1, drag: null as { x: number; y: number } | null });
  const hasFacing = facing !== null;

  useEffect(() => {
    if (facing) {
      const a = anglesFor(facing.lat, facing.lon);
      view.current.phi = a.phi;
      view.current.theta = Math.max(-0.5, Math.min(0.5, a.theta / 2));
    }
    // Only the first facing point sets the start; later ones are `target`s.
  }, [hasFacing]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let raf = 0;
    let cancelled = false;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    void import("cobe")
      .then(({ default: createGlobe }) => {
        if (cancelled) return;
        const size = canvas.offsetWidth || 480;
        const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
        const v = view.current;
        try {
          globeRef.current = createGlobe(canvas, {
            devicePixelRatio: dpr,
            width: size * dpr,
            height: size * dpr,
            phi: v.phi,
            theta: v.theta,
            dark: 1,
            diffuse: 1.6,
            mapSamples: 20_000,
            mapBrightness: 6,
            baseColor: [0.07, 0.3, 0.45],
            markerColor: COLOURS.station,
            glowColor: [0.1, 0.45, 0.75],
            markerElevation: elevation,
            scale: live.current.scale,
            markers: toCobe(live.current.markers),
          }) as Globe;
          setFailed(false);
        } catch (error) {
          // No WebGL (a VM, a blocked GPU): the panels around still read out.
          console.info("Globe unavailable.", error);
          setFailed(true);
          return;
        }
        const tick = () => {
          if (document.visibilityState === "visible") {
            const now = live.current;
            const t = now.target;
            if (t && t.seq !== v.seq) {
              v.seq = t.seq;
              const a = anglesFor(t.lat, t.lon);
              v.goal = { phi: v.phi + shortest(v.phi, a.phi), theta: a.theta };
            }
            if (v.goal && !v.drag) {
              const k = reduced ? 1 : 0.08;
              v.phi += (v.goal.phi - v.phi) * k;
              v.theta += (v.goal.theta - v.theta) * k;
              if (Math.abs(v.goal.phi - v.phi) < 0.002 && Math.abs(v.goal.theta - v.theta) < 0.002) v.goal = null;
            } else if (!v.drag && now.spin && !reduced) {
              v.phi += 0.0018;
            }
            globeRef.current?.update({ phi: v.phi, theta: v.theta, scale: now.scale, markers: toCobe(now.markers) });
          }
          raf = window.requestAnimationFrame(tick);
        };
        raf = window.requestAnimationFrame(tick);
      })
      .catch((error: unknown) => {
        console.info("Globe chunk did not load.", error);
        setFailed(true);
      });
    return () => {
      cancelled = true;
      window.cancelAnimationFrame(raf);
      globeRef.current?.destroy();
      globeRef.current = null;
      releaseWebglContext(canvas);
    };
  }, [generation]);

  const turn = (dPhi: number, dTheta: number) => {
    const v = view.current;
    v.goal = null;
    v.phi += dPhi;
    v.theta = Math.max(-1.2, Math.min(1.2, v.theta + dTheta));
  };

  return (
    <div
      ref={hostRef}
      className={cn("relative", interactive && "cursor-grab touch-none focus-visible:outline focus-visible:outline-1 focus-visible:outline-primary/60 active:cursor-grabbing", className)}
      data-testid="stark-orbit-globe"
      data-failed={failed ? "true" : "false"}
      {...(interactive
        ? {
            role: "img",
            "aria-label": label,
            tabIndex: 0,
            onPointerDown: (e: React.PointerEvent<HTMLDivElement>) => {
              view.current.drag = { x: e.clientX, y: e.clientY };
              view.current.goal = null;
              e.currentTarget.setPointerCapture?.(e.pointerId);
            },
            onPointerMove: (e: React.PointerEvent<HTMLDivElement>) => {
              const d = view.current.drag;
              if (!d) return;
              turn((e.clientX - d.x) * 0.006, (e.clientY - d.y) * 0.006);
              view.current.drag = { x: e.clientX, y: e.clientY };
            },
            onPointerUp: () => {
              view.current.drag = null;
            },
            onPointerCancel: () => {
              view.current.drag = null;
            },
            onKeyDown: (e: React.KeyboardEvent<HTMLDivElement>) => {
              const step = 0.12;
              const moves: Record<string, [number, number]> = {
                ArrowLeft: [-step, 0],
                ArrowRight: [step, 0],
                ArrowUp: [0, -step],
                ArrowDown: [0, step],
              };
              const m = moves[e.key];
              if (!m) return;
              e.preventDefault();
              turn(m[0], m[1]);
            },
          }
        : { "aria-hidden": true })}
    >
      <canvas key={generation} ref={canvasRef} className="h-full w-full" style={{ contain: "layout paint size" }} />
    </div>
  );
}

function toCobe(markers: OrbitMarker[]) {
  return markers.map((m) => ({ location: [m.lat, m.lon] as [number, number], size: m.size, color: COLOURS[m.kind] }));
}
