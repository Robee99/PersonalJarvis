import { useEffect, useMemo, useRef, useState } from "react";
import { MotionConfig } from "framer-motion";
import { Activity, ExternalLink, Flame, Globe2, Home, Minus, Pause, Play, Plus, RefreshCw, Satellite, LocateFixed } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  clearPendingGeVView,
  readPendingGeVView,
} from "@/lib/gevViewBridge";
import { StarkShell } from "@/components/stark/StarkShell";
import { StarkPanel } from "@/components/stark/StarkPanel";
import { NewsBroadcast, NewsFeeds, NewsTicker } from "@/components/stark/WorldPanels";
import { OrbitGlobe, type GlobeTarget, type OrbitMarker } from "@/components/stark/OrbitGlobe";
import { readCity } from "@/components/stark/WeatherGlobe";
import { useNow } from "@/components/stark/StarkShell";
import { propagate } from "@/lib/orbit";
import {
  agoMs,
  eventSeverity,
  feedState,
  quakeSeverity,
  useIss,
  useNaturalEvents,
  useQuakes,
  useStations,
  type FeedState,
} from "@/lib/publicData";
import { useElementSize } from "@/hooks/useElementSize";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

/** The local GEV server is the one globe runtime controlled by the GEV MCP. */
export const DEFAULT_GEV_EMBED_URL = "http://127.0.0.1:4173/";

/** How long GEV gets to say it is ready before the built-in globe takes over. */
export const GEV_READY_TIMEOUT_MS = 8_000;

/** Only loopback GEV servers can be embedded; reject remote origins. */
export function resolveGeVEmbedUrl(configured?: string): string | null {
  try {
    const url = new URL(configured || DEFAULT_GEV_EMBED_URL);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) ||
      url.username ||
      url.password
    )
      return null;
    url.searchParams.set("embed", "1");
    return url.href;
  } catch {
    return null;
  }
}

export const GEV_EMBED_URL = resolveGeVEmbedUrl(
  import.meta.env.VITE_GEV_EMBED_URL,
);

type GevState = "connecting" | "ready" | "down";
type GlobeSource = "gev" | "builtin";
type StatusKey = "connecting" | "connected" | "applying" | "unconfirmed" | "applied" | "not_applied" | "down";

/** A camera request (from the GEV tool) as a point on the built-in globe. */
function pointOf(view: Record<string, unknown> | null): { lat: number; lon: number } | null {
  const camera = view?.camera as Record<string, unknown> | undefined;
  const lat = Number(camera?.lat);
  const lon = Number(camera?.lon);
  return Number.isFinite(lat) && Number.isFinite(lon) ? { lat, lon } : null;
}

/**
 * The World screen: a globe with the world's public events on it, and the
 * news beside it.
 *
 * The globe is God's Eye View (the local GEV app, its own layers and
 * controls) when it is running; when it does not answer, the built-in globe
 * takes its place with the HUD's own layers (quakes, natural events, the ISS
 * and crewed stations, your city) and controls (drag or arrow keys to turn,
 * zoom, reset, pause the spin). A camera request from the GEV tool goes to
 * whichever globe is on screen.
 */
export function WorldIntelligenceView() {
  const t = useT();
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [status, setStatus] = useState<StatusKey>("connecting");
  const [gev, setGev] = useState<GevState>("connecting");
  const [picked, setPicked] = useState<GlobeSource | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [target, setTarget] = useState<GlobeTarget | null>(null);
  const targetSeq = useRef(0);
  const source: GlobeSource = picked ?? (gev === "down" ? "builtin" : "gev");
  const sourceRef = useRef(source);
  sourceRef.current = source;

  const aim = (point: { lat: number; lon: number }) => {
    targetSeq.current += 1;
    setTarget({ ...point, seq: targetSeq.current });
  };
  const aimRef = useRef(aim);
  aimRef.current = aim;

  useEffect(() => {
    if (!GEV_EMBED_URL) return;
    const origin = new URL(GEV_EMBED_URL).origin;
    let ready = false;
    let queuedView: Record<string, unknown> | null = readPendingGeVView();
    let activeRequest: string | null = null;
    let timeout: number | undefined;
    let sequence = 0;
    setGev("connecting");
    setStatus("connecting");
    const readyTimer = window.setTimeout(() => {
      if (ready) return;
      setGev("down");
      setStatus("down");
      // A view asked for before GEV gave up still lands, on the built-in globe.
      const point = pointOf(queuedView);
      if (point) {
        queuedView = null;
        clearPendingGeVView();
        aimRef.current(point);
      }
    }, GEV_READY_TIMEOUT_MS);

    const sendView = (view: Record<string, unknown>) => {
      const target = frameRef.current?.contentWindow;
      if (sourceRef.current === "builtin") {
        const point = pointOf(view);
        if (point) {
          clearPendingGeVView();
          aimRef.current(point);
          return;
        }
      }
      if (!ready || !target) {
        queuedView = view;
        return;
      }
      const id = `jarvis-gev-${Date.now()}-${++sequence}`;
      activeRequest = id;
      clearPendingGeVView();
      setStatus("applying");
      target.postMessage({ type: "gev:view", id, view }, origin);
      window.clearTimeout(timeout);
      timeout = window.setTimeout(() => {
        if (activeRequest === id) setStatus("unconfirmed");
      }, 15_000);
    };

    const onMessage = (event: MessageEvent) => {
      if (
        event.source !== frameRef.current?.contentWindow ||
        event.origin !== origin ||
        !event.data ||
        typeof event.data !== "object"
      )
        return;
      if (event.data.type === "gev:ready") {
        ready = true;
        window.clearTimeout(readyTimer);
        setGev("ready");
        setStatus("connected");
        if (queuedView) {
          const view = queuedView;
          queuedView = null;
          sendView(view);
        }
      } else if (
        event.data.type === "gev:view-applied" &&
        event.data.id === activeRequest
      ) {
        window.clearTimeout(timeout);
        activeRequest = null;
        setStatus(event.data.ok === true ? "applied" : "not_applied");
      }
    };

    const onRequestedView = (event: Event) => {
      const view = (event as CustomEvent<{ view?: unknown }>).detail?.view;
      if (
        view &&
        typeof view === "object" &&
        !Array.isArray(view) &&
        "camera" in view &&
        view.camera &&
        typeof view.camera === "object"
      )
        sendView(view as Record<string, unknown>);
    };

    window.addEventListener("message", onMessage);
    window.addEventListener("jarvis:gev-view", onRequestedView);
    return () => {
      window.removeEventListener("message", onMessage);
      window.removeEventListener("jarvis:gev-view", onRequestedView);
      window.clearTimeout(timeout);
      window.clearTimeout(readyTimer);
    };
  }, [attempt]);

  if (!GEV_EMBED_URL) {
    return (
      <section className="flex h-full min-h-0 min-w-0 flex-col items-center justify-center gap-3 bg-background p-8 text-center">
        <Globe2 className="h-8 w-8 text-muted-foreground" aria-hidden="true" />
        <h1 className="text-heading">{t("stark.world.unavailable")}</h1>
        <p className="max-w-lg text-body text-muted-foreground">{t("stark.world.unavailable_hint")}</p>
      </section>
    );
  }

  const switcher = (
    <div className="flex items-center gap-1" role="group" aria-label={t("stark.world.globe_source")}>
      {(["gev", "builtin"] as const).map((key) => (
        <button
          key={key}
          type="button"
          aria-pressed={source === key}
          onClick={() => setPicked(key)}
          data-testid={`stark-globe-${key}`}
          className={cn(
            "border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-[0.14em] transition-colors",
            source === key ? "border-primary/60 bg-primary/10 text-primary" : "border-primary/15 text-muted-foreground hover:text-foreground",
          )}
        >
          {t(`stark.world.source_${key}`)}
        </button>
      ))}
    </div>
  );

  return (
    <MotionConfig reducedMotion="user">
      <StarkShell
        screen="world"
        subtitle={t("stark.world.title")}
        testId="stark-world"
        badge={
          <div className="flex flex-col items-end gap-1">
            <p aria-live="polite" className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground" data-testid="stark-gev-status" data-state={gev}>
              God’s Eye View · {t(`stark.world.gev_${status}`)}
            </p>
            <a
              className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.16em] text-primary hover:underline"
              href={GEV_EMBED_URL}
              target="_blank"
              rel="noopener noreferrer"
            >
              {t("stark.world.open_separately")}
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            </a>
          </div>
        }
      >
        <div className="grid min-h-0 flex-1 gap-3 px-4 pb-3 xl:grid-cols-[1fr_minmax(300px,24rem)] xl:px-6">
          <StarkPanel
            title={t(source === "gev" ? "stark.world.gev_title" : "stark.world.globe_title")}
            meta={switcher}
            className="min-h-[360px]"
            bodyClassName="relative flex px-1.5 pb-1.5"
            testId="stark-gev"
          >
            {/* GEV stays mounted while the built-in globe is up, so it can still connect. */}
            <iframe
              key={attempt}
              ref={frameRef}
              className={cn("min-h-0 min-w-0 flex-1 border-0 bg-black", source !== "gev" && "hidden")}
              title="God’s Eye View"
              src={GEV_EMBED_URL}
              loading="eager"
              referrerPolicy="no-referrer"
              allow="microphone; fullscreen"
              allowFullScreen
            />
            {source === "gev" && gev !== "ready" && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-background/85 p-6 text-center" data-testid="stark-gev-waiting">
                <p className="text-sm text-foreground">{t(gev === "down" ? "stark.world.gev_down_hint" : "stark.world.gev_waiting")}</p>
                {gev === "down" && (
                  <div className="flex gap-2">
                    <button type="button" onClick={() => setAttempt((n) => n + 1)} className="flex items-center gap-1.5 border border-primary/50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.14em] text-primary hover:bg-primary/10">
                      <RefreshCw className="h-3.5 w-3.5" aria-hidden />
                      {t("stark.world.retry")}
                    </button>
                    <button type="button" onClick={() => setPicked("builtin")} className="border border-primary/50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.14em] text-primary hover:bg-primary/10">
                      {t("stark.world.use_builtin")}
                    </button>
                  </div>
                )}
              </div>
            )}
            {source === "builtin" && (
              <BuiltInGlobe
                target={target}
                onAim={aim}
                gevDown={gev === "down"}
                onRetryGev={() => {
                  setPicked(null);
                  setAttempt((n) => n + 1);
                }}
              />
            )}
          </StarkPanel>
          <div className="flex min-h-0 flex-col gap-3">
            <NewsBroadcast className="min-h-[240px] flex-1" />
            <NewsFeeds className="shrink-0" />
          </div>
        </div>
        <div className="shrink-0 px-4 pb-3 xl:px-6">
          <NewsTicker />
        </div>
      </StarkShell>
    </MotionConfig>
  );
}

type Layer = "quakes" | "events" | "space" | "home";
const LAYERS: { key: Layer; icon: LucideIcon }[] = [
  { key: "quakes", icon: Activity },
  { key: "events", icon: Flame },
  { key: "space", icon: Satellite },
  { key: "home", icon: Home },
];

interface Spot {
  id: string;
  layer: Layer;
  label: string;
  sub: string;
  lat: number;
  lon: number;
  at: number | null;
  marker: OrbitMarker;
}

const MIN_SCALE = 0.8;
const MAX_SCALE = 2.6;

/**
 * The built-in globe: the HUD's public feeds as layers on the dotted Earth,
 * each layer with a toggle and a count, a list of what is on it (click to
 * turn there), and the globe's own controls.
 */
function BuiltInGlobe({
  target,
  onAim,
  gevDown,
  onRetryGev,
}: {
  target: GlobeTarget | null;
  onAim: (point: { lat: number; lon: number }) => void;
  gevDown: boolean;
  onRetryGev: () => void;
}) {
  const t = useT();
  const quakesQ = useQuakes();
  const eventsQ = useNaturalEvents();
  const issQ = useIss();
  const stationsQ = useStations();
  const now = useNow(5_000).getTime();
  const city = readCity();
  const [hidden, setHidden] = useState<Set<Layer>>(() => new Set());
  const [spin, setSpin] = useState(true);
  const [scale, setScale] = useState(1);
  const stageRef = useRef<HTMLDivElement>(null);
  const stage = useElementSize(stageRef);
  const size = Math.max(220, Math.min(stage.width, stage.height) - 8 || 420);

  const spots = useMemo<Spot[]>(() => {
    const out: Spot[] = [];
    for (const q of quakesQ.data?.items ?? []) {
      const high = quakeSeverity(q) === "high";
      out.push({
        id: `q-${q.id}`,
        layer: "quakes",
        label: q.mag != null ? `M${q.mag.toFixed(1)} ${t("stark.intel.quake")}` : t("stark.intel.quake"),
        sub: q.place,
        lat: q.lat,
        lon: q.lon,
        at: q.time_ms,
        marker: { lat: q.lat, lon: q.lon, size: 0.02 + Math.min(0.06, Math.max(0, (q.mag ?? 3) - 3) * 0.012), kind: high ? "quake_high" : "quake" },
      });
    }
    for (const e of eventsQ.data?.items ?? []) {
      out.push({
        id: `e-${e.id}`,
        layer: "events",
        label: e.title,
        sub: e.category,
        lat: e.lat,
        lon: e.lon,
        at: e.date_ms,
        marker: { lat: e.lat, lon: e.lon, size: eventSeverity(e) === "low" ? 0.025 : 0.04, kind: "event" },
      });
    }
    const iss = issQ.data?.available && issQ.data.lat != null && issQ.data.lon != null ? issQ.data : null;
    if (iss) {
      out.push({
        id: "iss",
        layer: "space",
        label: "ISS",
        sub: `${Math.round(iss.alt_km ?? 0)} km`,
        lat: iss.lat!,
        lon: iss.lon!,
        at: iss.time_ms ?? null,
        marker: { lat: iss.lat!, lon: iss.lon!, size: 0.07, kind: "iss" },
      });
    }
    for (const s of (stationsQ.data?.items ?? []).slice(0, 16)) {
      if (s.norad_id === 25544) continue;
      const p = propagate(s, now);
      out.push({
        id: `s-${s.norad_id}`,
        layer: "space",
        label: s.name,
        sub: `${Math.round(p.alt_km)} km`,
        lat: p.lat,
        lon: p.lon,
        at: null,
        marker: { lat: p.lat, lon: p.lon, size: 0.03, kind: "station" },
      });
    }
    if (city) {
      out.push({
        id: "home",
        layer: "home",
        label: city.name,
        sub: t("stark.world.your_city"),
        lat: city.lat,
        lon: city.lon,
        at: null,
        marker: { lat: city.lat, lon: city.lon, size: 0.045, kind: "home" },
      });
    }
    return out;
  }, [quakesQ.data, eventsQ.data, issQ.data, stationsQ.data, city?.lat, city?.lon, city?.name, now, t]); // eslint-disable-line react-hooks/exhaustive-deps

  const visible = useMemo(() => spots.filter((s) => !hidden.has(s.layer)), [spots, hidden]);
  const latest = useMemo(
    () => [...visible].filter((s) => s.layer === "quakes" || s.layer === "events").sort((a, b) => (b.at ?? 0) - (a.at ?? 0)).slice(0, 6),
    [visible],
  );
  const feeds: { layer: Layer; state: FeedState }[] = [
    { layer: "quakes", state: feedState(quakesQ) },
    { layer: "events", state: feedState(eventsQ) },
    { layer: "space", state: feedState(issQ) },
  ];
  const toggle = (layer: Layer) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(layer)) next.delete(layer);
      else next.add(layer);
      return next;
    });
  const zoom = (by: number) => setScale((s) => Math.max(MIN_SCALE, Math.min(MAX_SCALE, +(s + by).toFixed(2))));
  const facing = city ?? { lat: 20, lon: 0 };

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2 px-2.5 pb-1 lg:flex-row" data-testid="stark-builtin-globe">
      {/* Layers and what is on the globe. */}
      <div className="flex shrink-0 flex-col gap-2 lg:w-60">
        {gevDown && (
          <p className="text-[11px] leading-snug text-muted-foreground" data-testid="stark-gev-down-note">
            {t("stark.world.builtin_note")}{" "}
            <button type="button" onClick={onRetryGev} className="font-semibold text-primary hover:underline">
              {t("stark.world.retry")}
            </button>
          </p>
        )}
        <div className="flex flex-wrap gap-1.5" role="group" aria-label={t("stark.intel.layers")}>
          {LAYERS.map(({ key, icon: Icon }) => {
            const on = !hidden.has(key);
            const count = spots.filter((s) => s.layer === key).length;
            return (
              <button
                key={key}
                type="button"
                aria-pressed={on}
                onClick={() => toggle(key)}
                data-testid={`stark-world-layer-${key}`}
                className={cn(
                  "flex items-center gap-1.5 border px-2 py-0.5 text-xs font-semibold uppercase tracking-[0.12em] transition-colors",
                  on ? "border-primary/60 bg-primary/10 text-primary" : "border-primary/15 text-muted-foreground hover:text-foreground",
                )}
              >
                <Icon className="h-3 w-3" aria-hidden />
                {t(`stark.world.layer_${key}`)}
                <span className="tabular-nums text-foreground/70">{count}</span>
              </button>
            );
          })}
        </div>
        <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">{t("stark.world.latest")}</p>
        <ul className="min-h-0 flex-1 overflow-y-auto text-xs" data-testid="stark-world-latest">
          {!latest.length && <li className="py-1 text-muted-foreground">{t(quakesQ.isPending || eventsQ.isPending ? "stark.intel.loading" : "stark.world.nothing_shown")}</li>}
          {latest.map((s) => (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => onAim(s)}
                className="flex w-full items-center gap-2 border-b border-primary/5 py-1 text-left hover:bg-primary/5"
                data-testid="stark-world-spot"
                title={t("stark.world.turn_to")}
              >
                <LocateFixed className="h-3 w-3 shrink-0 text-primary/70" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-foreground">{s.label}</span>
                  <span className="block truncate text-[10px] text-muted-foreground">{s.sub}</span>
                </span>
                <span className="shrink-0 tabular-nums text-muted-foreground">{agoMs(s.at, now)}</span>
              </button>
            </li>
          ))}
        </ul>
        <ul className="space-y-0.5 text-[11px]" data-testid="stark-world-feeds">
          {feeds.map((f) => (
            <li key={f.layer} className="flex items-center justify-between" data-state={f.state}>
              <span className="text-muted-foreground">{t(`stark.world.layer_${f.layer}`)}</span>
              <span className={cn("font-semibold uppercase tracking-[0.1em]", f.state === "live" ? "text-[hsl(var(--stark-ok))]" : f.state === "down" ? "text-[hsl(var(--stark-hot))]" : "text-muted-foreground")}>
                {t(`stark.feed.${f.state}`)}
              </span>
            </li>
          ))}
        </ul>
      </div>

      {/* The globe and its controls. */}
      <div ref={stageRef} className="relative flex min-h-[18rem] min-w-0 flex-1 items-center justify-center">
        <div style={{ width: size, height: size }}>
          <OrbitGlobe
            markers={visible.map((s) => s.marker)}
            facing={facing}
            spin={spin}
            scale={scale}
            target={target}
            interactive
            elevation={0.015}
            label={fill(t("stark.world.globe_label"), { count: visible.length })}
            className="h-full w-full"
          />
        </div>
        <div className="absolute right-1 top-1 flex flex-col gap-1" role="toolbar" aria-label={t("stark.world.controls")}>
          <GlobeButton icon={Plus} label={t("stark.world.zoom_in")} onClick={() => zoom(0.2)} disabled={scale >= MAX_SCALE} testId="stark-globe-zoom-in" />
          <GlobeButton icon={Minus} label={t("stark.world.zoom_out")} onClick={() => zoom(-0.2)} disabled={scale <= MIN_SCALE} testId="stark-globe-zoom-out" />
          <GlobeButton icon={spin ? Pause : Play} label={t(spin ? "stark.world.pause" : "stark.world.resume")} onClick={() => setSpin((v) => !v)} pressed={!spin} testId="stark-globe-spin" />
          <GlobeButton
            icon={Home}
            label={t("stark.world.reset")}
            onClick={() => {
              setScale(1);
              onAim(facing);
            }}
            testId="stark-globe-reset"
          />
        </div>
        <p className="absolute bottom-1 left-1 text-[10px] text-muted-foreground">{t("stark.world.drag_hint")}</p>
        <p className="absolute bottom-1 right-1 text-[10px] text-muted-foreground">{t("stark.world.attribution")}</p>
        <span className="sr-only" data-testid="stark-globe-scale">
          {scale}
        </span>
      </div>
    </div>
  );
}

function GlobeButton({
  icon: Icon,
  label,
  onClick,
  disabled,
  pressed,
  testId,
}: {
  icon: LucideIcon;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  pressed?: boolean;
  testId: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      aria-pressed={pressed}
      title={label}
      data-testid={testId}
      className="flex h-8 w-8 items-center justify-center rounded-full border border-primary/40 bg-background/60 text-primary transition-colors hover:bg-primary/15 disabled:opacity-30"
    >
      <Icon className="h-4 w-4" aria-hidden />
    </button>
  );
}
