import { useEffect, useRef, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { MapPin, X } from "lucide-react";
import { StarkPanel } from "@/components/stark/StarkPanel";
import { useT } from "@/i18n";

/**
 * 04 — WEATHER beside a turning globe with a pin on the owner's city.
 *
 * Weather comes from Open-Meteo: free, no key, no account (the owner's rule:
 * free sources only). Nothing is sent until a city is set here, and only the
 * city name (to look it up) and its coordinates (for the forecast) are sent.
 * The city is remembered in this browser only.
 */
export interface WeatherCity {
  name: string;
  country: string;
  lat: number;
  lon: number;
}

const CITY_KEY = "stark.weather.city.v1";

export function readCity(): WeatherCity | null {
  try {
    const raw = window.localStorage.getItem(CITY_KEY);
    if (!raw) return null;
    const c = JSON.parse(raw) as WeatherCity;
    return typeof c.lat === "number" && typeof c.lon === "number" && c.name ? c : null;
  } catch {
    return null;
  }
}

function writeCity(city: WeatherCity | null): void {
  try {
    if (city) window.localStorage.setItem(CITY_KEY, JSON.stringify(city));
    else window.localStorage.removeItem(CITY_KEY);
  } catch {
    // Storage off: the city lasts for this session only.
  }
}

interface Forecast {
  current: {
    temperature_2m: number;
    apparent_temperature: number;
    relative_humidity_2m: number;
    wind_speed_10m: number;
    weather_code: number;
  };
  daily: {
    time: string[];
    temperature_2m_max: number[];
    temperature_2m_min: number[];
    weather_code: number[];
  };
}

async function fetchForecast(city: WeatherCity): Promise<Forecast> {
  const url =
    "https://api.open-meteo.com/v1/forecast" +
    `?latitude=${city.lat.toFixed(3)}&longitude=${city.lon.toFixed(3)}` +
    "&current=temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code" +
    "&daily=temperature_2m_max,temperature_2m_min,weather_code&forecast_days=4&timezone=auto";
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as Forecast;
}

async function geocode(name: string): Promise<WeatherCity | null> {
  const res = await fetch(
    `https://geocoding-api.open-meteo.com/v1/search?count=1&format=json&name=${encodeURIComponent(name)}`,
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const body = (await res.json()) as {
    results?: Array<{ name: string; country?: string; latitude: number; longitude: number }>;
  };
  const hit = body.results?.[0];
  return hit ? { name: hit.name, country: hit.country ?? "", lat: hit.latitude, lon: hit.longitude } : null;
}

/** WMO weather code → the i18n key of a short condition word. */
export function weatherKey(code: number): string {
  if (code === 0) return "clear";
  if (code <= 2) return "partly";
  if (code === 3) return "overcast";
  if (code <= 48) return "fog";
  if (code <= 57) return "drizzle";
  if (code <= 67) return "rain";
  if (code <= 77) return "snow";
  if (code <= 82) return "showers";
  if (code <= 86) return "snow";
  return "storm";
}

export function WeatherGlobe({ className }: { className?: string }) {
  const t = useT();
  const [city, setCity] = useState<WeatherCity | null>(() => readCity());
  const [editing, setEditing] = useState(false);
  const q = useQuery<Forecast>({
    queryKey: ["stark", "weather", city?.lat, city?.lon],
    queryFn: () => fetchForecast(city as WeatherCity),
    enabled: Boolean(city),
    refetchInterval: 15 * 60_000,
    staleTime: 10 * 60_000,
    retry: 1,
  });
  const f = q.data;

  return (
    <StarkPanel
      index="04"
      title={t("stark.weather.title")}
      meta={city ? `${city.name}${city.country ? `, ${city.country}` : ""}` : undefined}
      action={
        city && !editing ? (
          <button
            type="button"
            onClick={() => setEditing(true)}
            title={t("stark.weather.change")}
            aria-label={t("stark.weather.change")}
            className="text-muted-foreground transition-colors hover:text-primary"
          >
            <MapPin className="h-3 w-3" />
          </button>
        ) : undefined
      }
      className={className}
      testId="stark-weather"
      bodyClassName="flex items-center gap-3"
    >
      <StarkGlobe pin={city} className="h-[112px] w-[112px] shrink-0" />
      <div className="min-w-0 flex-1">
        {!city || editing ? (
          <CityForm
            onPick={(c) => {
              writeCity(c);
              setCity(c);
              setEditing(false);
            }}
            onCancel={city ? () => setEditing(false) : undefined}
          />
        ) : !f ? (
          <p className="text-xs text-muted-foreground">
            {t(q.isError ? "stark.weather.error" : "stark.weather.loading")}
          </p>
        ) : (
          <div data-testid="stark-weather-now">
            <div className="flex items-baseline gap-2">
              <span className="font-mono text-3xl tabular-nums text-foreground">
                {Math.round(f.current.temperature_2m)}°
              </span>
              <span className="font-mono text-xs uppercase tracking-wider text-primary">
                {t(`stark.weather.${weatherKey(f.current.weather_code)}`)}
              </span>
            </div>
            <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              {t("stark.weather.feels")} {Math.round(f.current.apparent_temperature)}° ·{" "}
              {Math.round(f.current.relative_humidity_2m)}% · {Math.round(f.current.wind_speed_10m)} km/h
            </p>
            <ul className="mt-1.5 grid grid-cols-3 gap-1">
              {f.daily.time.slice(1, 4).map((day, i) => (
                <li key={day} className="border border-primary/15 px-1 py-0.5 text-center">
                  <span className="block font-mono text-[9px] uppercase text-muted-foreground">
                    {new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { weekday: "short" })}
                  </span>
                  <span className="block font-mono text-[11px] tabular-nums text-foreground">
                    {Math.round(f.daily.temperature_2m_max[i + 1])}°/{Math.round(f.daily.temperature_2m_min[i + 1])}°
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </StarkPanel>
  );
}

/**
 * The Command screen's corner weather: the globe, the city, the temperature
 * and the sky in a few words. Same source and same rules as the panel above;
 * the pin button changes the city.
 */
export function WeatherMini({ className }: { className?: string }) {
  const t = useT();
  const [city, setCity] = useState<WeatherCity | null>(() => readCity());
  const [editing, setEditing] = useState(false);
  const q = useQuery<Forecast>({
    queryKey: ["stark", "weather", city?.lat, city?.lon],
    queryFn: () => fetchForecast(city as WeatherCity),
    enabled: Boolean(city),
    refetchInterval: 15 * 60_000,
    staleTime: 10 * 60_000,
    retry: 1,
  });
  const f = q.data;
  return (
    <div className={`flex items-center gap-3 ${className ?? ""}`} data-testid="stark-weather">
      <StarkGlobe pin={city} className="h-[92px] w-[92px] shrink-0" />
      <div className="min-w-0 font-hud">
        {!city || editing ? (
          <CityForm
            onPick={(c) => {
              writeCity(c);
              setCity(c);
              setEditing(false);
            }}
            onCancel={city ? () => setEditing(false) : undefined}
          />
        ) : (
          <>
            <button
              type="button"
              onClick={() => setEditing(true)}
              title={t("stark.weather.change")}
              className="flex items-center gap-1 text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground hover:text-primary"
            >
              {city.name}
              <MapPin className="h-3 w-3" aria-hidden />
            </button>
            {!f ? (
              <p className="text-xs text-muted-foreground">
                {t(q.isError ? "stark.weather.error" : "stark.weather.loading")}
              </p>
            ) : (
              <div data-testid="stark-weather-now">
                <p className="text-2xl font-semibold tabular-nums text-foreground">
                  {Math.round(f.current.temperature_2m)}°C
                </p>
                <p className="text-xs font-medium text-muted-foreground">
                  {t(`stark.weather.${weatherKey(f.current.weather_code)}`)} · {Math.round(f.current.relative_humidity_2m)}%
                </p>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function CityForm({ onPick, onCancel }: { onPick: (c: WeatherCity) => void; onCancel?: () => void }) {
  const t = useT();
  const [name, setName] = useState("");
  const [state, setState] = useState<"idle" | "busy" | "missing" | "error">("idle");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setState("busy");
    try {
      const hit = await geocode(name.trim());
      if (hit) onPick(hit);
      else setState("missing");
    } catch {
      setState("error");
    }
  };
  return (
    <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-1.5" data-testid="stark-weather-form">
      <label className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground" htmlFor="stark-city">
        {t("stark.weather.set_city")}
      </label>
      <div className="flex gap-1">
        <input
          id="stark-city"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t("stark.weather.placeholder")}
          className="min-w-0 flex-1 border border-primary/30 bg-transparent px-2 py-1 font-mono text-xs text-foreground outline-none focus:border-primary"
        />
        <button
          type="submit"
          disabled={state === "busy"}
          className="border border-primary/50 px-2 font-mono text-[10px] uppercase tracking-wider text-primary hover:bg-primary/10 disabled:opacity-50"
        >
          {t("stark.weather.set")}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} aria-label={t("common.cancel")} className="px-1 text-muted-foreground hover:text-foreground">
            <X className="h-3 w-3" />
          </button>
        )}
      </div>
      {(state === "missing" || state === "error") && (
        <p className="text-[10px] text-[hsl(var(--stark-hot))]">
          {t(state === "missing" ? "stark.weather.not_found" : "stark.weather.error")}
        </p>
      )}
      <p className="text-[10px] leading-snug text-muted-foreground">{t("stark.weather.privacy")}</p>
    </form>
  );
}

/**
 * A dotted holographic globe (cobe, MIT, ~5 KB, WebGL) that turns slowly,
 * with a pin on the city. Its own chunk; the loop stops while the window is
 * hidden and under reduced motion the globe stands still.
 */
export function StarkGlobe({ pin, className }: { pin: WeatherCity | null; className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let globe: { update: (s: Record<string, unknown>) => void; destroy: () => void } | null = null;
    let raf = 0;
    let cancelled = false;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    // Start facing the pin: cobe's phi turns the globe, so a longitude λ faces
    // the viewer at phi = -λ (in radians) shifted a quarter turn.
    let phi = pin ? -((pin.lon + 90) * Math.PI) / 180 + Math.PI : 0;
    const theta = pin ? Math.max(-0.6, Math.min(0.6, (pin.lat * Math.PI) / 180 / 2)) : 0.25;
    void import("cobe")
      .then(({ default: createGlobe }) => {
        if (cancelled || !canvas) return;
        const size = canvas.offsetWidth || 112;
        const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
        try {
          globe = createGlobe(canvas, {
            devicePixelRatio: dpr,
            width: size * dpr,
            height: size * dpr,
            phi,
            theta,
            dark: 1,
            diffuse: 1.4,
            mapSamples: 12_000,
            mapBrightness: 7,
            baseColor: [0.06, 0.32, 0.4],
            markerColor: [1, 0.55, 0.15],
            glowColor: [0.07, 0.55, 0.7],
            markers: pin ? [{ location: [pin.lat, pin.lon], size: 0.08 }] : [],
          }) as typeof globe;
        } catch {
          // No WebGL (a VM, a blocked GPU): the panel keeps its weather.
          return;
        }
        if (reduced) return;
        const tick = () => {
          if (document.visibilityState === "visible") {
            phi += 0.0035;
            globe?.update({ phi });
          }
          raf = window.requestAnimationFrame(tick);
        };
        raf = window.requestAnimationFrame(tick);
      })
      .catch(() => {
        // The chunk did not load: no globe, the weather still shows.
      });
    return () => {
      cancelled = true;
      window.cancelAnimationFrame(raf);
      globe?.destroy();
    };
  }, [pin?.lat, pin?.lon]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className={className} aria-hidden data-testid="stark-globe">
      <canvas ref={canvasRef} className="h-full w-full" style={{ contain: "layout paint size" }} />
    </div>
  );
}
