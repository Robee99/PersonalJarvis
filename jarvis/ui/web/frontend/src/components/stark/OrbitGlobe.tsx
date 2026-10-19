import { useEffect, useRef } from "react";

/**
 * The Orbital screen's globe: the dotted Earth (cobe, WebGL) with satellites
 * raised above it as markers. The ISS is the bright one; its path for the
 * next orbit is a trail of small dots. The globe turns slowly and faces the
 * ISS when it first draws; under reduced motion it stands still.
 *
 * Markers are pushed into the running globe on every change, so the live
 * position moves without rebuilding the WebGL scene.
 */
export interface OrbitMarker {
  lat: number;
  lon: number;
  size: number;
  /** "iss" is reactor orange, "track" a faint cyan, anything else cyan. */
  kind: "iss" | "station" | "track" | "home";
}

const COLOURS: Record<OrbitMarker["kind"], [number, number, number]> = {
  iss: [1, 0.55, 0.15],
  station: [0.35, 0.85, 1],
  track: [0.25, 0.65, 0.95],
  home: [0.3, 1, 0.6],
};

type Globe = { update: (s: Record<string, unknown>) => void; destroy: () => void };

export function OrbitGlobe({
  markers,
  facing,
  className,
}: {
  markers: OrbitMarker[];
  /** Where to face first: the ISS, or the owner's city. */
  facing: { lat: number; lon: number } | null;
  className?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const globeRef = useRef<Globe | null>(null);
  const markersRef = useRef(markers);
  markersRef.current = markers;
  const hasFacing = facing !== null;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let raf = 0;
    let cancelled = false;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    // cobe turns the globe by phi; this phi puts `facing.lon` at the centre.
    let phi = facing ? (3 * Math.PI) / 2 - (facing.lon * Math.PI) / 180 : 0;
    const theta = facing ? Math.max(-0.5, Math.min(0.5, (facing.lat * Math.PI) / 180 / 2)) : 0.25;
    void import("cobe")
      .then(({ default: createGlobe }) => {
        if (cancelled) return;
        const size = canvas.offsetWidth || 480;
        const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
        try {
          globeRef.current = createGlobe(canvas, {
            devicePixelRatio: dpr,
            width: size * dpr,
            height: size * dpr,
            phi,
            theta,
            dark: 1,
            diffuse: 1.6,
            mapSamples: 20_000,
            mapBrightness: 6,
            baseColor: [0.07, 0.3, 0.45],
            markerColor: COLOURS.station,
            glowColor: [0.1, 0.45, 0.75],
            markerElevation: 0.06,
            markers: toCobe(markersRef.current),
          }) as Globe;
        } catch (error) {
          // No WebGL (a VM, a blocked GPU): the panels around still read out.
          console.info("Orbital globe unavailable.", error);
          return;
        }
        const tick = () => {
          if (document.visibilityState === "visible") {
            if (!reduced) phi += 0.0018;
            globeRef.current?.update({ phi, markers: toCobe(markersRef.current) });
          }
          raf = window.requestAnimationFrame(tick);
        };
        raf = window.requestAnimationFrame(tick);
      })
      .catch((error: unknown) => {
        console.info("Orbital globe chunk did not load.", error);
      });
    return () => {
      cancelled = true;
      window.cancelAnimationFrame(raf);
      globeRef.current?.destroy();
      globeRef.current = null;
    };
    // Built once per mount (and once more when a facing point first arrives).
  }, [hasFacing]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className={className} aria-hidden data-testid="stark-orbit-globe">
      <canvas ref={canvasRef} className="h-full w-full" style={{ contain: "layout paint size" }} />
    </div>
  );
}

function toCobe(markers: OrbitMarker[]) {
  return markers.map((m) => ({ location: [m.lat, m.lon] as [number, number], size: m.size, color: COLOURS[m.kind] }));
}
