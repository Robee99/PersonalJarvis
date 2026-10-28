import { useEffect, useRef, useState, type ReactNode } from "react";
import { LAND_COLS, LAND_ROWS, isLandCell } from "@/lib/worldLand";
import { cn } from "@/lib/utils";

/**
 * A dotted world map (equirectangular, 80°N to 60°S) drawn on a canvas from
 * the land bitmap, with optional markers and lines on top. The HUD uses it
 * faint behind the reactor on the Command screen, as the Intelligence map
 * where each marker is a real public event (an earthquake, a storm, a fire),
 * and as the Orbital screen's ground-track map.
 */
export interface MapMarker {
  id: string;
  lon: number;
  lat: number;
  /** "hot" draws reactor-orange, "warn" amber, anything else cyan. */
  tone?: "hot" | "warn" | "calm";
  /** 0..1, how big the marker's halo is. */
  weight?: number;
  label?: string;
}

const LAT_TOP = 80;
const LAT_BOTTOM = -60;

/** Equirectangular position of a coordinate in the map's 0..1 box. */
export function project(lon: number, lat: number): { x: number; y: number } {
  return {
    x: (lon + 180) / 360,
    y: (LAT_TOP - Math.max(LAT_BOTTOM, Math.min(LAT_TOP, lat))) / (LAT_TOP - LAT_BOTTOM),
  };
}

/** A line across the map (a ground track), drawn dashed when `dashed`. */
export interface MapPath {
  id: string;
  points: Array<{ lon: number; lat: number }>;
  tone?: "hot" | "calm";
  dashed?: boolean;
}

export function WorldDotMap({
  markers = [],
  paths = [],
  selected,
  onSelect,
  dotAlpha = 0.5,
  className,
  children,
  testId,
}: {
  markers?: MapMarker[];
  paths?: MapPath[];
  selected?: string | null;
  onSelect?: (id: string) => void;
  /** How bright the land dots are (the Command backdrop keeps them faint). */
  dotAlpha?: number;
  className?: string;
  children?: ReactNode;
  testId?: string;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    const box = boxRef.current;
    if (!box || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ w: Math.round(width), h: Math.round(height) });
    });
    ro.observe(box);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext?.("2d");
    if (!canvas || !ctx || !size.w || !size.h) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = size.w * dpr;
    canvas.height = size.h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.w, size.h);
    const color = getComputedStyle(canvas).color || "rgb(56, 220, 245)";
    ctx.fillStyle = color;
    const rowTop = Math.floor(((90 - LAT_TOP) / 180) * LAND_ROWS);
    const rowBottom = Math.ceil(((90 - LAT_BOTTOM) / 180) * LAND_ROWS);
    const cellW = size.w / LAND_COLS;
    const cellH = size.h / (rowBottom - rowTop);
    const r = Math.max(0.6, Math.min(cellW, cellH) * 0.32);
    for (let row = rowTop; row < rowBottom; row++) {
      for (let col = 0; col < LAND_COLS; col++) {
        if (!isLandCell(col, row)) continue;
        // Brighter towards the equator band, dimmer towards the poles.
        const lat = 90 - (row + 0.5) * (180 / LAND_ROWS);
        ctx.globalAlpha = dotAlpha * (0.55 + 0.45 * Math.cos((lat * Math.PI) / 180));
        ctx.beginPath();
        ctx.arc((col + 0.5) * cellW, (row - rowTop + 0.5) * cellH, r, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.globalAlpha = 1;
  }, [size, dotAlpha]);

  return (
    <div ref={boxRef} className={cn("relative overflow-hidden", className)} data-testid={testId}>
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full text-primary" aria-hidden />
      {paths.length > 0 && (
        <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden>
          {paths.map((path) => (
            <polyline
              key={path.id}
              data-testid="stark-map-path"
              points={path.points
                .map((pt) => {
                  const p = project(pt.lon, pt.lat);
                  return `${(p.x * 1000).toFixed(1)},${(p.y * 1000).toFixed(1)}`;
                })
                .join(" ")}
              fill="none"
              stroke={path.tone === "hot" ? "hsl(var(--stark-hot))" : "hsl(var(--primary))"}
              strokeWidth={1.5}
              strokeDasharray={path.dashed ? "4 4" : undefined}
              vectorEffect="non-scaling-stroke"
              opacity={0.85}
            />
          ))}
        </svg>
      )}
      {markers.map((m) => {
        const p = project(m.lon, m.lat);
        const color =
          m.tone === "hot" ? "hsl(var(--stark-hot))" : m.tone === "warn" ? "hsl(42 100% 58%)" : "hsl(var(--primary))";
        const halo = 6 + Math.round((m.weight ?? 0.3) * 18);
        const isSel = selected === m.id;
        return (
          <button
            key={m.id}
            type="button"
            title={m.label}
            aria-label={m.label}
            aria-pressed={isSel}
            onClick={() => onSelect?.(m.id)}
            className="stark-marker absolute -translate-x-1/2 -translate-y-1/2 rounded-full focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
            style={{
              left: `${p.x * 100}%`,
              top: `${p.y * 100}%`,
              width: halo,
              height: halo,
              background: `radial-gradient(circle, ${color} 0 22%, transparent 70%)`,
              boxShadow: isSel ? `0 0 0 1px ${color}, 0 0 14px ${color}` : `0 0 10px ${color}`,
            }}
            data-testid="stark-map-marker"
            data-tone={m.tone ?? "calm"}
          />
        );
      })}
      {children}
    </div>
  );
}
