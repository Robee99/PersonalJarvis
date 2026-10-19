import { useEffect, useMemo, useState, type ReactNode } from "react";
import { MotionConfig } from "framer-motion";
import { Activity, Camera, Globe2, Radio, Satellite, ShieldAlert, Video } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { StarkShell, useNow } from "@/components/stark/StarkShell";
import { StarkLamp, StarkPanel } from "@/components/stark/StarkPanel";
import { WorldDotMap, type MapMarker } from "@/components/stark/WorldDotMap";
import { Chip, YouTubeFrame } from "@/components/stark/LivePanels";
import { CctvFrame, useFrameTick } from "@/components/stark/CctvWall";
import { FOCUS_REFRESH_MS, useCctvCatalog, useCctvHealth, type CctvCamera, type CctvCatalog } from "@/lib/cctv";
import { useLiveCatalog } from "@/lib/live";
import { lastSeen, placed, useIntelEvents, type IntelEvent, type IntelSource } from "@/lib/intel";
import { propagate, type GroundPoint } from "@/lib/orbit";
import { REGIONS, regionOf, useStations, type Station } from "@/lib/publicData";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * GHOST: every public sensor JARVIS can see, on one screen. Public road and
 * city cameras (God's Eye View's catalog), live city cameras, crewed space
 * stations overhead and reported incidents are counted, put on one fusion
 * map, and opened one at a time in the focus panel.
 *
 * Everything here is a public feed about places and events. The screen
 * follows no person: it has no face, body or gait matching, no tracking of
 * someone between cameras and no profile of anyone.
 */

type Layer = "all" | "cameras" | "stations" | "incidents";
type Focus =
  | { kind: "camera"; id: string }
  | { kind: "city"; id: string }
  | { kind: "station"; id: number }
  | { kind: "incident"; id: string };

const ROTATE_MS = 20_000;
const ALERT_SEVERITY = 2;

interface StationNow {
  station: Station;
  at: GroundPoint;
}

/** One camera from each of the largest cities, turning to a new camera every `ROTATE_MS`. */
export function featuredCameras(data: CctvCatalog | undefined, count: number, turn: number): CctvCamera[] {
  if (!data?.cameras.length) return [];
  const byCity = new Map<string, CctvCamera[]>();
  for (const cam of data.cameras) {
    if (cam.feed !== "image") continue;
    const list = byCity.get(cam.city_id) ?? [];
    list.push(cam);
    byCity.set(cam.city_id, list);
  }
  const cities = [...byCity.values()].sort((a, b) => b.length - a.length).slice(0, count);
  return cities.map((list) => list[turn % list.length]);
}

/** How many of each thing were counted, for the readouts. */
export function tally(cams: CctvCamera[]): { providers: [string, number][]; regions: [string, number][] } {
  const providers = new Map<string, number>();
  const regions = new Map<string, number>();
  for (const cam of cams) {
    providers.set(cam.provider || "—", (providers.get(cam.provider || "—") ?? 0) + 1);
    const region = regionOf(cam.lon, cam.lat);
    if (region) regions.set(region, (regions.get(region) ?? 0) + 1);
  }
  const sorted = (m: Map<string, number>) => [...m.entries()].sort((a, b) => b[1] - a[1]);
  return { providers: sorted(providers), regions: sorted(regions) };
}

function Stat({ icon: Icon, label, value, sub, tone }: { icon: LucideIcon; label: string; value: ReactNode; sub?: ReactNode; tone?: "hot" }) {
  return (
    <div className="stark-panel flex min-w-0 items-center gap-3 px-3 py-2">
      <Icon className={cn("h-5 w-5 shrink-0", tone === "hot" ? "text-[hsl(var(--stark-hot))]" : "text-primary")} aria-hidden />
      <div className="min-w-0">
        <p className="truncate font-hud text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">{label}</p>
        <p className={cn("font-hud text-lg font-semibold tabular-nums leading-tight", tone === "hot" ? "text-[hsl(var(--stark-hot))]" : "text-foreground")}>{value}</p>
        {sub && <p className="truncate text-[10px] text-muted-foreground">{sub}</p>}
      </div>
    </div>
  );
}

function Bars({ rows, total, label }: { rows: [string, number][]; total: number; label: (key: string) => string }) {
  return (
    <ul className="flex flex-col gap-1.5">
      {rows.map(([key, n]) => (
        <li key={key} className="text-[11px]">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-foreground">{label(key)}</span>
            <span className="font-mono tabular-nums text-muted-foreground">{n.toLocaleString()}</span>
          </div>
          <div className="mt-0.5 h-1 bg-primary/10">
            <div className="h-full bg-primary/70" style={{ width: `${total ? Math.max(2, (n / total) * 100) : 0}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function sourceLamp(status: IntelSource["status"]): "on" | "warn" | "off" {
  return status === "ok" ? "on" : status === "stale" ? "warn" : "off";
}

export function GhostView() {
  const t = useT();
  const now = useNow(30_000);
  const cctv = useCctvCatalog();
  const health = useCctvHealth();
  const live = useLiveCatalog();
  const stationsQ = useStations();
  const intel = useIntelEvents();
  const data = cctv.data;
  const cams = data?.cameras ?? [];
  const cities = data?.cities ?? [];
  const imageCams = cams.filter((c) => c.feed === "image");
  const videoCams = cams.length - imageCams.length;
  const ytCams = (live.data?.streams ?? []).filter((s) => s.kind === "camera");
  const iss = ytCams.find((s) => s.key === "iss");
  const cityCam = ytCams.find((s) => s.key !== "iss");

  const [layer, setLayer] = useState<Layer>("all");
  const [focus, setFocus] = useState<Focus | null>(null);
  const [turn, setTurn] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.visibilityState !== "hidden") setTurn((n) => n + 1);
    }, ROTATE_MS);
    return () => window.clearInterval(id);
  }, []);
  const tick = useFrameTick(ROTATE_MS);
  const focusTick = useFrameTick(FOCUS_REFRESH_MS, focus?.kind === "camera" || focus?.kind === "city");

  const featured = useMemo(() => featuredCameras(data, 3, turn), [data, turn]);
  const stations: StationNow[] = useMemo(
    () => (stationsQ.data?.items ?? []).map((station) => ({ station, at: propagate(station, now.getTime()) })),
    [stationsQ.data, now],
  );
  const events = intel.data?.events ?? [];
  const sources = intel.data?.sources ?? [];
  const day = now.getTime() - 24 * 3_600_000;
  const recent = events.filter((e) => lastSeen(e) >= day);
  const alerts = recent
    .filter((e) => (e.severity ?? 0) >= ALERT_SEVERITY)
    .sort((a, b) => (b.severity ?? 0) - (a.severity ?? 0) || lastSeen(b) - lastSeen(a));
  const counts = useMemo(() => tally(imageCams), [imageCams]);
  const healthy = health.data ? health.data.ok : null;

  const markers: MapMarker[] = useMemo(() => {
    const out: MapMarker[] = [];
    if (layer === "all" || layer === "cameras") {
      const top = Math.max(1, ...cities.map((c) => c.count));
      for (const c of cities) {
        out.push({ id: `city:${c.id}`, lat: c.lat, lon: c.lon, tone: "calm", weight: 0.2 + 0.8 * Math.sqrt(c.count / top), label: fill(t("stark.ghost.city_marker"), { city: c.name, count: c.count }) });
      }
    }
    if (layer === "all" || layer === "stations") {
      for (const s of stations) {
        out.push({ id: `station:${s.station.norad_id}`, lat: s.at.lat, lon: s.at.lon, tone: "warn", weight: 0.35, label: s.station.name });
      }
    }
    if (layer === "all" || layer === "incidents") {
      for (const e of alerts.filter(placed).slice(0, 150)) {
        out.push({ id: `incident:${e.id}`, lat: e.lat, lon: e.lon, tone: "hot", weight: 0.15 + 0.2 * (e.severity ?? 0), label: e.title });
      }
    }
    return out;
  }, [layer, cities, stations, alerts, t]);

  const pick = (markerId: string) => {
    const [kind, ...rest] = markerId.split(":");
    const id = rest.join(":");
    if (kind === "city") setFocus({ kind: "city", id });
    else if (kind === "station") setFocus({ kind: "station", id: Number(id) });
    else if (kind === "incident") setFocus({ kind: "incident", id });
  };
  const selectedMarker =
    focus?.kind === "city" ? `city:${focus.id}` : focus?.kind === "station" ? `station:${focus.id}` : focus?.kind === "incident" ? `incident:${focus.id}` : null;

  return (
    <MotionConfig reducedMotion="user">
      <StarkShell
        screen="ghost"
        subtitle={t("stark.ghost.subtitle")}
        badge={
          <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground" data-testid="stark-ghost-state">
            <StarkLamp state={data?.available ? "on" : "warn"} />
            {t(data?.available ? "stark.ghost.fusion_on" : "stark.ghost.fusion_partial")}
          </p>
        }
        testId="stark-ghost"
      >
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 pb-3 pt-1 xl:px-6 2xl:overflow-hidden">
          {/* The counts. */}
          <div className="grid shrink-0 grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6" data-testid="stark-ghost-stats">
            <Stat icon={Globe2} label={t("stark.ghost.cities")} value={cities.length || "—"} sub={t("stark.ghost.cities_sub")} />
            <Stat icon={Camera} label={t("stark.ghost.cameras")} value={cams.length ? (cams.length + ytCams.length).toLocaleString() : "—"} sub={fill(t("stark.ghost.cameras_sub"), { image: imageCams.length.toLocaleString(), video: (videoCams + ytCams.length).toLocaleString() })} />
            <Stat icon={Video} label={t("stark.ghost.frames_live")} value={healthy ?? "—"} sub={t("stark.ghost.frames_sub")} />
            <Stat icon={Satellite} label={t("stark.ghost.stations")} value={stations.length || "—"} sub={t("stark.ghost.stations_sub")} />
            <Stat icon={Radio} label={t("stark.ghost.feeds")} value={sources.length ? `${sources.filter((s) => s.status === "ok").length}/${sources.length}` : "—"} sub={t("stark.ghost.feeds_sub")} />
            <Stat icon={ShieldAlert} label={t("stark.ghost.alerts")} value={alerts.length} sub={t("stark.ghost.alerts_sub")} tone={alerts.length ? "hot" : undefined} />
          </div>

          {/* The feeds. */}
          <div className="grid shrink-0 grid-cols-1 gap-2 sm:grid-cols-2 xl:h-[11.5rem] xl:grid-cols-5" data-testid="stark-ghost-feeds">
            {featured.map((cam, i) => (
              <FeedTile key={`cctv-${i}`} title={fill(t("stark.ghost.feed_cctv"), { city: cam.city || cam.city_id })} live>
                {data?.gev && <CctvFrame gev={data.gev} camera={cam} tick={tick} className="absolute inset-x-1.5 bottom-1.5 top-0" onOpen={() => setFocus({ kind: "camera", id: cam.id })} />}
              </FeedTile>
            ))}
            {featured.length === 0 && (
              <FeedTile title={t("stark.ghost.feed_cctv_none")}>
                <p className="p-3 text-[11px] text-muted-foreground">{t("stark.cctv.gev_down")}</p>
              </FeedTile>
            )}
            {cityCam && (
              <FeedTile title={fill(t("stark.ghost.feed_city"), { city: cityCam.name })} live>
                <YouTubeFrame stream={cityCam} autoplay={false} className="h-full" />
              </FeedTile>
            )}
            {iss && (
              <FeedTile title={t("stark.ghost.feed_orbit")} live>
                <YouTubeFrame stream={iss} autoplay={false} className="h-full" />
              </FeedTile>
            )}
          </div>

          {/* The map and the focus. */}
          <div className="grid min-h-[26rem] gap-3 xl:min-h-0 xl:flex-1 xl:grid-cols-[minmax(0,1fr)_minmax(300px,24rem)]">
            <StarkPanel
              title={t("stark.ghost.map")}
              meta={<span className="flex items-center gap-1.5"><StarkLamp state="on" />{t("stark.ghost.realtime")}</span>}
              bodyClassName="flex min-h-0 flex-col gap-2"
              testId="stark-ghost-map"
            >
              <div className="flex flex-wrap gap-1" role="group" aria-label={t("stark.ghost.layers")}>
                {(["all", "cameras", "stations", "incidents"] as Layer[]).map((l) => (
                  <Chip key={l} active={layer === l} onClick={() => setLayer(l)} testId={`stark-ghost-layer-${l}`}>
                    {t(`stark.ghost.layer_${l}`)}
                  </Chip>
                ))}
              </div>
              <WorldDotMap markers={markers} selected={selectedMarker} onSelect={pick} dotAlpha={0.45} className="min-h-[14rem] flex-1" testId="stark-ghost-fusion" />
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-muted-foreground">
                <Legend tone="calm" label={t("stark.ghost.legend_cameras")} />
                <Legend tone="warn" label={t("stark.ghost.legend_stations")} />
                <Legend tone="hot" label={t("stark.ghost.legend_incidents")} />
              </div>
            </StarkPanel>
            <FocusPanel
              focus={focus}
              data={data}
              stations={stations}
              events={events}
              tick={focusTick}
              onCamera={(id) => setFocus({ kind: "camera", id })}
              onClear={() => setFocus(null)}
            />
          </div>

          {/* The readouts. */}
          <div className="grid shrink-0 gap-3 md:grid-cols-2 xl:h-[13rem] xl:grid-cols-4">
            <StarkPanel title={t("stark.ghost.by_operator")} meta={counts.providers.length || undefined} bodyClassName="overflow-y-auto" testId="stark-ghost-operators">
              <Bars rows={counts.providers.slice(0, 8)} total={imageCams.length} label={(k) => k} />
            </StarkPanel>
            <StarkPanel title={t("stark.ghost.by_region")} bodyClassName="overflow-y-auto" testId="stark-ghost-regions">
              <Bars rows={REGIONS.map((r) => [r, counts.regions.find(([k]) => k === r)?.[1] ?? 0] as [string, number]).filter(([, n]) => n > 0)} total={imageCams.length} label={(k) => t(`stark.region.${k}`)} />
            </StarkPanel>
            <StarkPanel title={t("stark.ghost.feed_health")} bodyClassName="overflow-y-auto" testId="stark-ghost-health">
              <ul className="flex flex-col gap-1 text-[11px]">
                <li className="flex items-center gap-2">
                  <StarkLamp state={data?.available ? "on" : "off"} />
                  <span className="flex-1 text-foreground">God’s Eye View</span>
                  <span className="font-mono tabular-nums text-muted-foreground">{data?.available ? cams.length.toLocaleString() : t("stark.ghost.down")}</span>
                </li>
                <li className="flex items-center gap-2">
                  <StarkLamp state={live.data ? "on" : "off"} />
                  <span className="flex-1 text-foreground">{t("stark.ghost.youtube")}</span>
                  <span className="font-mono tabular-nums text-muted-foreground">{live.data?.streams.length ?? "—"}</span>
                </li>
                <li className="flex items-center gap-2">
                  <StarkLamp state={stationsQ.data ? "on" : "off"} />
                  <span className="flex-1 text-foreground">{t("stark.ghost.celestrak")}</span>
                  <span className="font-mono tabular-nums text-muted-foreground">{stations.length || "—"}</span>
                </li>
                {sources.map((s) => (
                  <li key={s.id} className="flex items-center gap-2">
                    <StarkLamp state={sourceLamp(s.status)} />
                    <span className="flex-1 truncate text-foreground">{s.name}</span>
                    <span className="font-mono tabular-nums text-muted-foreground">{s.count}</span>
                  </li>
                ))}
              </ul>
            </StarkPanel>
            <StarkPanel title={t("stark.ghost.pulse")} meta={t("stark.ghost.last_24h")} bodyClassName="overflow-y-auto" testId="stark-ghost-pulse">
              <Bars rows={kindCounts(recent).slice(0, 7)} total={recent.length} label={(k) => k.replace(/_/g, " ")} />
            </StarkPanel>
          </div>

          {/* Every source in one strip, then the alerts. */}
          <div className="stark-panel flex shrink-0 flex-wrap items-center gap-x-5 gap-y-1 px-3 py-2 font-mono text-[10px] uppercase tracking-[0.14em]" data-testid="stark-ghost-strip">
            <span className="flex items-center gap-1.5 font-semibold text-primary"><Activity className="h-3 w-3" aria-hidden />{t("stark.ghost.fusion")}</span>
            <StripItem label={t("stark.ghost.strip_cctv")} value={imageCams.length} />
            <StripItem label={t("stark.ghost.strip_video")} value={videoCams + ytCams.length} />
            <StripItem label={t("stark.ghost.strip_news")} value={(live.data?.streams.length ?? 0) - ytCams.length} />
            <StripItem label={t("stark.ghost.strip_stations")} value={stations.length} />
            <StripItem label={t("stark.ghost.strip_incidents")} value={events.length} />
            <span className="ml-auto flex min-w-0 items-center gap-2 text-[hsl(var(--stark-hot))]">
              <ShieldAlert className="h-3 w-3 shrink-0" aria-hidden />
              <span className="truncate normal-case tracking-normal" data-testid="stark-ghost-alert">
                {alerts[0] ? `${alerts[0].title}${alerts[0].place ? ` · ${alerts[0].place}` : ""}` : t("stark.ghost.no_alerts")}
              </span>
            </span>
          </div>
        </div>
      </StarkShell>
    </MotionConfig>
  );
}

function kindCounts(events: IntelEvent[]): [string, number][] {
  const m = new Map<string, number>();
  for (const e of events) m.set(e.kind, (m.get(e.kind) ?? 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

function StripItem({ label, value }: { label: string; value: number }) {
  return (
    <span className="text-muted-foreground">
      {label} <span className="font-semibold tabular-nums text-foreground">{value.toLocaleString()}</span>
    </span>
  );
}

function Legend({ tone, label }: { tone: "calm" | "warn" | "hot"; label: string }) {
  const color = tone === "hot" ? "bg-[hsl(var(--stark-hot))]" : tone === "warn" ? "bg-[hsl(42_100%_58%)]" : "bg-primary";
  return (
    <span className="flex items-center gap-1.5">
      <span aria-hidden className={cn("h-2 w-2 rounded-full", color)} />
      {label}
    </span>
  );
}

function FeedTile({ title, live, children }: { title: string; live?: boolean; children: ReactNode }) {
  return (
    <section className="stark-panel flex h-44 min-h-0 flex-col xl:h-auto">
      <header className="flex items-center gap-2 px-2.5 py-1.5">
        <h3 className="min-w-0 flex-1 truncate font-hud text-[10px] font-semibold uppercase tracking-[0.2em] text-foreground/90">{title}</h3>
        {live && <span className="flex items-center gap-1 font-mono text-[9px] font-semibold uppercase tracking-[0.14em] text-[hsl(var(--stark-hot))]"><span aria-hidden className="h-1.5 w-1.5 rounded-full bg-[hsl(var(--stark-hot))]" />Live</span>}
      </header>
      <div className="relative min-h-0 flex-1 px-1.5 pb-1.5">{children}</div>
    </section>
  );
}

function FocusPanel({
  focus,
  data,
  stations,
  events,
  tick,
  onCamera,
  onClear,
}: {
  focus: Focus | null;
  data: CctvCatalog | undefined;
  stations: StationNow[];
  events: IntelEvent[];
  tick: number;
  onCamera: (id: string) => void;
  onClear: () => void;
}) {
  const t = useT();
  let body: ReactNode = <p className="text-xs text-muted-foreground">{t("stark.ghost.focus_hint")}</p>;
  let meta: ReactNode = undefined;

  if (focus && (focus.kind === "camera" || focus.kind === "city") && data?.gev) {
    const inCity =
      focus.kind === "city"
        ? data.cameras.filter((c) => c.city_id === focus.id && c.feed === "image")
        : (() => {
            const cam = data.cameras.find((c) => c.id === focus.id);
            return cam ? data.cameras.filter((c) => c.city_id === cam.city_id && c.feed === "image") : [];
          })();
    const cam = focus.kind === "camera" ? data.cameras.find((c) => c.id === focus.id) : inCity[0];
    if (cam) {
      const idx = Math.max(0, inCity.findIndex((c) => c.id === cam.id));
      meta = inCity.length > 1 ? `${idx + 1}/${inCity.length}` : undefined;
      body = (
        <div className="flex min-h-0 flex-1 flex-col gap-2" data-testid="stark-ghost-focus-camera">
          <CctvFrame gev={data.gev} camera={cam} tick={tick} className="aspect-video w-full shrink-0" />
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px]">
            <Row k={t("stark.ghost.f_camera")} v={cam.name} />
            <Row k={t("stark.ghost.f_city")} v={cam.city || cam.city_id} />
            <Row k={t("stark.ghost.f_operator")} v={cam.provider || "—"} />
            <Row k={t("stark.ghost.f_position")} v={`${cam.lat.toFixed(4)}, ${cam.lon.toFixed(4)}`} />
            {cam.heading !== null && <Row k={t("stark.ghost.f_facing")} v={`${Math.round(cam.heading)}°`} />}
            <Row k={t("stark.ghost.f_refresh")} v={t("stark.ghost.f_refresh_v")} />
          </dl>
          {inCity.length > 1 && (
            <div className="flex gap-2">
              <button type="button" className="font-mono text-[10px] uppercase tracking-[0.14em] text-primary hover:underline" onClick={() => onCamera(inCity[(idx - 1 + inCity.length) % inCity.length].id)} data-testid="stark-ghost-prev">
                {t("stark.cctv.prev")}
              </button>
              <button type="button" className="font-mono text-[10px] uppercase tracking-[0.14em] text-primary hover:underline" onClick={() => onCamera(inCity[(idx + 1) % inCity.length].id)} data-testid="stark-ghost-next">
                {t("stark.cctv.next")}
              </button>
            </div>
          )}
        </div>
      );
    }
  } else if (focus?.kind === "station") {
    const s = stations.find((x) => x.station.norad_id === focus.id);
    if (s) {
      body = (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px]" data-testid="stark-ghost-focus-station">
          <Row k={t("stark.ghost.f_station")} v={s.station.name} />
          <Row k={t("stark.ghost.f_norad")} v={String(s.station.norad_id)} />
          <Row k={t("stark.ghost.f_over")} v={`${s.at.lat.toFixed(2)}, ${s.at.lon.toFixed(2)}`} />
          <Row k={t("stark.ghost.f_altitude")} v={`${Math.round(s.at.alt_km)} km`} />
          <Row k={t("stark.ghost.f_period")} v={`${s.station.period_min.toFixed(1)} min`} />
          <Row k={t("stark.ghost.f_inclination")} v={`${s.station.inclination.toFixed(1)}°`} />
          <p className="col-span-2 mt-1 text-[10px] text-muted-foreground">{t("stark.orbit.approx")}</p>
        </dl>
      );
    }
  } else if (focus?.kind === "incident") {
    const e = events.find((x) => x.id === focus.id);
    if (e) {
      body = (
        <div className="flex flex-col gap-2 text-[11px]" data-testid="stark-ghost-focus-incident">
          <p className="font-semibold text-foreground">{e.title}</p>
          {e.summary && <p className="text-muted-foreground">{e.summary}</p>}
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            {e.place && <Row k={t("stark.ghost.f_place")} v={e.place} />}
            <Row k={t("stark.ghost.f_severity")} v={e.severity_label ?? String(e.severity ?? "—")} />
            <Row k={t("stark.ghost.f_source")} v={e.source} />
            <Row k={t("stark.ghost.f_evidence")} v={t(`stark.ghost.evidence_${e.evidence}`)} />
            <Row k={t("stark.ghost.f_seen")} v={new Date(lastSeen(e)).toLocaleString()} />
          </dl>
        </div>
      );
    }
  }

  return (
    <StarkPanel
      title={t("stark.ghost.focus")}
      meta={meta}
      action={
        focus ? (
          <button type="button" onClick={onClear} className="font-mono text-[10px] uppercase tracking-[0.14em] text-primary hover:underline" data-testid="stark-ghost-clear">
            {t("stark.ghost.clear")}
          </button>
        ) : undefined
      }
      bodyClassName="flex min-h-0 flex-col overflow-y-auto"
      testId="stark-ghost-focus"
    >
      {body}
    </StarkPanel>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <>
      <dt className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">{k}</dt>
      <dd className="min-w-0 truncate text-foreground" title={v}>{v}</dd>
    </>
  );
}
