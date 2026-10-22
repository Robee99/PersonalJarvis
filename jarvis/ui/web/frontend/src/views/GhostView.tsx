import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { MotionConfig } from "framer-motion";
import {
  Camera,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Globe2,
  LayoutGrid,
  Map as MapIcon,
  PanelRight,
  Pause,
  Play,
  Radio,
  Satellite,
  Search,
  ShieldAlert,
  Shuffle,
  Video,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { StarkShell, useNow } from "@/components/stark/StarkShell";
import { StarkLamp } from "@/components/stark/StarkPanel";
import { WorldDotMap, type MapMarker } from "@/components/stark/WorldDotMap";
import { Chip } from "@/components/stark/LivePanels";
import { useDeadCameras, useFrameTick } from "@/components/stark/CctvWall";
import { CameraWall, CodeBadge, countryLabel, FocusView, PlacePicker, Row } from "@/components/stark/GhostWall";
import { FOCUS_REFRESH_MS, useCctvCatalog, useCctvHealth, WALL_REFRESH_MS, type CctvCamera } from "@/lib/cctv";
import {
  allCities,
  buildCams,
  filterCams,
  placesOf,
  randomCity,
  stepCity,
  wallOrder,
  type GhostCam,
  type GhostCity,
  type TileSize,
} from "@/lib/ghostWall";
import { useLiveCatalog } from "@/lib/live";
import { lastSeen, placed, useIntelEvents, type IntelEvent, type IntelSource } from "@/lib/intel";
import { propagate, type GroundPoint } from "@/lib/orbit";
import { REGIONS, regionOf, useStations, type Station } from "@/lib/publicData";
import { useIntelStore } from "@/store/intel";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * GHOST: every public camera JARVIS can see, filling the screen. Road and
 * city cameras from God's Eye View and the city cameras on YouTube make one
 * wall that can be narrowed to any country or city at once, searched, and
 * opened one camera at a time over most of the screen. The counts on top
 * open their own views: the place picker, the fusion map with crewed space
 * stations and reported incidents, and a side drawer with the operators,
 * regions, feed health and alerts.
 *
 * Everything here is a public feed about places and events. The screen
 * follows no person: it has no face, body or plate matching, no tracking of
 * anyone between cameras and no profile of anyone.
 */

type Layer = "all" | "cameras" | "stations" | "incidents";
type View = "wall" | "map";
type DrawerTab = "operators" | "regions" | "health" | "alerts";
type MapPick = { kind: "station"; id: number } | { kind: "incident"; id: string };

const ALERT_SEVERITY = 2;
const PREFS_KEY = "ghost.wall.v1";
const SIZES: TileSize[] = ["s", "m", "l"];

interface Prefs {
  size: TileSize;
  country: string | null;
  city: string | null;
}

function loadPrefs(): Prefs {
  const fallback: Prefs = { size: "m", country: null, city: null };
  try {
    const raw = window.localStorage.getItem(PREFS_KEY);
    if (!raw) return fallback;
    const p = JSON.parse(raw) as Partial<Prefs>;
    return {
      size: SIZES.includes(p.size as TileSize) ? (p.size as TileSize) : "m",
      country: typeof p.country === "string" ? p.country : null,
      city: typeof p.city === "string" ? p.city : null,
    };
  } catch (err) {
    // Blocked storage or an old value: start on the whole world.
    console.warn("ghost: saved wall settings unreadable", err);
    return fallback;
  }
}

function savePrefs(p: Prefs) {
  try {
    window.localStorage.setItem(PREFS_KEY, JSON.stringify(p));
  } catch (err) {
    // Blocked storage: the choice then lasts until the screen closes.
    console.warn("ghost: wall settings not saved", err);
  }
}

interface StationNow {
  station: Station;
  at: GroundPoint;
}

/** How many cameras each operator and each region has. */
export function tally(cams: Array<Pick<CctvCamera, "provider" | "lat" | "lon">>): { providers: [string, number][]; regions: [string, number][] } {
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

function sourceLamp(status: IntelSource["status"]): "on" | "warn" | "off" {
  return status === "ok" ? "on" : status === "stale" ? "warn" : "off";
}

function kindCounts(events: IntelEvent[]): [string, number][] {
  const m = new Map<string, number>();
  for (const e of events) m.set(e.kind, (m.get(e.kind) ?? 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

export function GhostView() {
  const t = useT();
  const now = useNow(30_000);
  const cctv = useCctvCatalog();
  const health = useCctvHealth();
  const live = useLiveCatalog();
  const stationsQ = useStations();
  const intel = useIntelEvents();
  const dead = useDeadCameras();
  const data = cctv.data;
  const other = t("stark.ghost.unknown_country");

  const [prefs] = useState(loadPrefs);
  const [size, setSize] = useState<TileSize>(prefs.size);
  const [country, setCountry] = useState<string | null>(prefs.country);
  const [city, setCity] = useState<string | null>(prefs.city);
  const [words, setWords] = useState("");
  const [view, setView] = useState<View>("wall");
  const [layer, setLayer] = useState<Layer>("all");
  const [drawer, setDrawer] = useState<DrawerTab | null>(null);
  const [picker, setPicker] = useState(false);
  const [paused, setPaused] = useState(false);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [mapPick, setMapPick] = useState<MapPick | null>(null);

  useEffect(() => savePrefs({ size, country, city }), [size, country, city]);

  const streams = live.data?.streams;
  const cams = useMemo(() => buildCams(data, streams ?? []), [data, streams]);
  // The order is fixed by the whole catalog, so a camera failing does not
  // shuffle the tiles under the pointer; the picker drops cities with no picture.
  const order = useMemo(() => wallOrder(cams, placesOf(cams)), [cams]);
  const places = useMemo(() => placesOf(cams, dead), [cams, dead]);
  const deadRef = useRef(dead);
  deadRef.current = dead;

  // A saved place that no longer has cameras falls back to the whole world.
  const knownCountry = country && places.some((c) => c.key === country) ? country : null;
  const knownCity = city && cams.some((c) => c.cityKey === city) ? city : null;
  const filter = useMemo(() => ({ country: knownCountry, city: knownCity, words }), [knownCountry, knownCity, words]);
  const shown = useMemo(
    () => filterCams(order, filter).filter((c) => !deadRef.current.has(c.id)),
    [order, filter],
  );
  const resetKey = `${filter.country}|${filter.city}|${filter.words}`;

  const placeCountry = places.find((c) => c.key === (knownCountry ?? cams.find((x) => x.cityKey === knownCity)?.countryKey));
  const placeCity = knownCity ? placeCountry?.cities.find((c) => c.key === knownCity) ?? null : null;

  const focusIndex = focusId ? shown.findIndex((c) => c.id === focusId) : -1;
  const focusCam: GhostCam | undefined = focusId ? (focusIndex >= 0 ? shown[focusIndex] : cams.find((c) => c.id === focusId)) : undefined;

  const wallTick = useFrameTick(WALL_REFRESH_MS, !paused && !focusCam && view === "wall");
  const focusTick = useFrameTick(FOCUS_REFRESH_MS, !paused && !!focusCam);

  const stations: StationNow[] = useMemo(
    () => (stationsQ.data?.items ?? []).map((station) => ({ station, at: propagate(station, now.getTime()) })),
    [stationsQ.data, now],
  );
  const events = useMemo(() => intel.data?.events ?? [], [intel.data]);
  const sources = intel.data?.sources ?? [];
  const day = now.getTime() - 24 * 3_600_000;
  const recent = useMemo(() => events.filter((e) => lastSeen(e) >= day), [events, day]);
  const alerts = useMemo(
    () =>
      recent
        .filter((e) => (e.severity ?? 0) >= ALERT_SEVERITY)
        .sort((a, b) => (b.severity ?? 0) - (a.severity ?? 0) || lastSeen(b) - lastSeen(a)),
    [recent],
  );
  const cityCount = allCities(places).length;
  const videoCount = cams.filter((c) => c.kind === "video").length;

  // The assistant asked for a YouTube city camera (world_globe "watch").
  const watch = useIntelStore((s) => s.watch);
  useEffect(() => {
    const cam = watch ? cams.find((c) => c.id === `yt:${watch.key}`) : undefined;
    if (!cam) return;
    setWords("");
    setCountry(cam.countryKey);
    setCity(cam.cityKey);
    setView("wall");
    setFocusId(cam.id);
    // The request and the catalog decide; `cams` follows the catalog.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watch?.seq, cams.length]);

  const goPlace = useCallback((countryKey: string | null, cityKey: string | null) => {
    setWords("");
    setCountry(countryKey);
    setCity(cityKey);
    setView("wall");
  }, []);
  const pickCity = (c: GhostCity) => {
    goPlace(c.countryKey, c.key);
    setPicker(false);
  };
  const firstIn = (cityKey: string) => filterCams(order, { country: null, city: cityKey, words: "" }).find((c) => !deadRef.current.has(c.id));
  const jumpCity = (target: GhostCity | null, keepCountry: boolean) => {
    if (!target) return;
    goPlace(keepCountry ? knownCountry : null, target.key);
    if (focusCam) setFocusId(firstIn(target.key)?.id ?? null);
  };
  const currentCity = focusCam?.cityKey ?? knownCity;
  const nextCity = (dir: 1 | -1) => jumpCity(stepCity(places, currentCity, knownCountry, dir), true);
  const randomPlace = () => jumpCity(randomCity(places, currentCity, Math.random), false);
  const step = (dir: 1 | -1) => {
    if (!shown.length) return;
    const at = focusIndex >= 0 ? focusIndex : 0;
    setFocusId(shown[(at + dir + shown.length) % shown.length].id);
  };
  const openDrawer = (tab: DrawerTab) => setDrawer((d) => (d === tab ? null : tab));

  // Arrows step through the cameras, Escape closes the large view (or the drawer).
  const stepRef = useRef(step);
  stepRef.current = step;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) return;
      if (focusId) {
        if (e.key === "ArrowRight") stepRef.current(1);
        else if (e.key === "ArrowLeft") stepRef.current(-1);
        else if (e.key === "Escape") setFocusId(null);
        else return;
        e.preventDefault();
      } else if (e.key === "Escape" && drawer && !picker) {
        setDrawer(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [focusId, drawer, picker]);

  const markers: MapMarker[] = useMemo(() => {
    const out: MapMarker[] = [];
    if (layer === "all" || layer === "cameras") {
      const cities = allCities(places).filter((c) => c.lat !== null && c.lon !== null);
      const top = Math.max(1, ...cities.map((c) => c.count));
      for (const c of cities) {
        out.push({ id: `city:${c.key}`, lat: c.lat!, lon: c.lon!, tone: "calm", weight: 0.2 + 0.8 * Math.sqrt(c.count / top), label: fill(t("stark.ghost.city_marker"), { city: c.name, count: c.count }) });
      }
    }
    if (layer === "all" || layer === "stations") {
      for (const s of stations) out.push({ id: `station:${s.station.norad_id}`, lat: s.at.lat, lon: s.at.lon, tone: "warn", weight: 0.35, label: s.station.name });
    }
    if (layer === "all" || layer === "incidents") {
      for (const e of alerts.filter(placed).slice(0, 150)) {
        out.push({ id: `incident:${e.id}`, lat: e.lat, lon: e.lon, tone: "hot", weight: 0.15 + 0.2 * (e.severity ?? 0), label: e.title });
      }
    }
    return out;
  }, [layer, places, stations, alerts, t]);

  const pickMarker = (markerId: string) => {
    const [kind, ...rest] = markerId.split(":");
    const id = rest.join(":");
    if (kind === "city") {
      const c = allCities(places).find((x) => x.key === id);
      if (c) goPlace(c.countryKey, c.key);
    } else if (kind === "station") setMapPick({ kind: "station", id: Number(id) });
    else if (kind === "incident") setMapPick({ kind: "incident", id });
  };
  const selectedMarker = mapPick ? `${mapPick.kind}:${mapPick.id}` : knownCity ? `city:${knownCity}` : null;
  const showMap = (l: Layer) => {
    setLayer(l);
    setView("map");
    setFocusId(null);
  };

  const healthy = health.data ? health.data.ok : null;
  const gevLine = !data
    ? t(cctv.isError ? "stark.cctv.error" : "stark.cctv.loading")
    : !data.available
      ? t(data.reason === "not_loopback" ? "stark.cctv.not_local" : "stark.cctv.gev_down")
      : null;

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
        <div className="flex min-h-0 flex-1 flex-col gap-1.5 px-2 pb-2 pt-1 xl:px-3">
          {/* The toolbar: where, what, and the counts that open their own views. */}
          <div className="flex shrink-0 flex-wrap items-center gap-1.5" data-testid="stark-ghost-toolbar">
            <div className="relative">
              <button
                type="button"
                onClick={() => setPicker((p) => !p)}
                aria-expanded={picker}
                aria-haspopup="dialog"
                className={cn(
                  "flex h-8 max-w-[22rem] items-center gap-2 border px-2.5 text-xs font-semibold transition-colors",
                  picker ? "border-primary/70 bg-primary/15 text-primary" : "border-primary/30 text-foreground hover:border-primary/60",
                )}
                data-testid="stark-ghost-place"
              >
                <Globe2 className="h-4 w-4 shrink-0 text-primary" aria-hidden />
                {placeCountry && <CodeBadge code={placeCountry.code} />}
                <span className="min-w-0 truncate">
                  {placeCountry ? countryLabel(placeCountry, other) : t("stark.ghost.all_countries")}
                  {placeCity && <span className="text-primary"> › {placeCity.name}</span>}
                </span>
                <span className="font-mono text-[10px] tabular-nums text-muted-foreground">{shown.length.toLocaleString()}</span>
                <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
              </button>
              {picker && (
                <PlacePicker
                  places={places}
                  country={knownCountry}
                  city={knownCity}
                  onCountry={(key) => goPlace(key, null)}
                  onCity={pickCity}
                  onClose={() => setPicker(false)}
                />
              )}
            </div>
            {(knownCountry || knownCity) && (
              <ToolButton label={t("stark.ghost.clear_place")} onClick={() => goPlace(null, null)} testId="stark-ghost-clear-place">
                <X className="h-3.5 w-3.5" aria-hidden />
              </ToolButton>
            )}
            <ToolButton label={t("stark.ghost.prev_city")} onClick={() => nextCity(-1)} testId="stark-ghost-prev-city">
              <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
            </ToolButton>
            <ToolButton label={t("stark.ghost.next_city")} onClick={() => nextCity(1)} testId="stark-ghost-next-city">
              <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            </ToolButton>
            <ToolButton label={t("stark.ghost.random")} onClick={randomPlace} testId="stark-ghost-random">
              <Shuffle className="h-3.5 w-3.5" aria-hidden />
            </ToolButton>
            <label className="flex h-8 w-full min-w-[12rem] items-center gap-1.5 border border-primary/30 px-2 sm:w-60">
              <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
              <input
                value={words}
                onChange={(e) => {
                  // A search looks everywhere, so it leaves the chosen place.
                  setWords(e.target.value);
                  setCountry(null);
                  setCity(null);
                  setView("wall");
                }}
                placeholder={t("stark.ghost.search")}
                aria-label={t("stark.ghost.search")}
                className="min-w-0 flex-1 bg-transparent text-xs text-foreground outline-none placeholder:text-muted-foreground"
                data-testid="stark-ghost-search"
              />
              {words && (
                <button type="button" onClick={() => setWords("")} aria-label={t("stark.cctv.clear")} className="text-muted-foreground hover:text-foreground">
                  <X className="h-3 w-3" aria-hidden />
                </button>
              )}
            </label>

            <div className="flex flex-wrap items-center gap-1" data-testid="stark-ghost-stats">
              <Stat icon={Globe2} label={t("stark.ghost.cities")} value={cityCount || "—"} hint={t("stark.ghost.cities_sub")} active={picker} onClick={() => setPicker((p) => !p)} testId="stark-ghost-stat-cities" />
              <Stat
                icon={Camera}
                label={t("stark.ghost.cameras")}
                value={cams.length ? cams.length.toLocaleString() : "—"}
                hint={fill(t("stark.ghost.cameras_sub"), { image: (cams.length - videoCount).toLocaleString(), video: videoCount.toLocaleString() })}
                active={view === "wall" && !knownCountry && !knownCity && !words}
                onClick={() => {
                  goPlace(null, null);
                  setFocusId(null);
                }}
                testId="stark-ghost-stat-cameras"
              />
              <Stat icon={Video} label={t("stark.ghost.frames_live")} value={healthy ?? "—"} hint={t("stark.ghost.frames_sub")} active={drawer === "health"} onClick={() => openDrawer("health")} testId="stark-ghost-stat-frames" />
              <Stat icon={Satellite} label={t("stark.ghost.stations")} value={stations.length || "—"} hint={t("stark.ghost.stations_sub")} active={view === "map" && layer === "stations"} onClick={() => showMap("stations")} testId="stark-ghost-stat-stations" />
              <Stat
                icon={Radio}
                label={t("stark.ghost.feeds")}
                value={sources.length ? `${sources.filter((s) => s.status === "ok").length}/${sources.length}` : "—"}
                hint={t("stark.ghost.feeds_sub")}
                active={drawer === "health"}
                onClick={() => openDrawer("health")}
                testId="stark-ghost-stat-feeds"
              />
              <Stat
                icon={ShieldAlert}
                label={t("stark.ghost.alerts")}
                value={alerts.length}
                hint={t("stark.ghost.alerts_sub")}
                tone={alerts.length ? "hot" : undefined}
                active={drawer === "alerts"}
                onClick={() => {
                  showMap("incidents");
                  setDrawer("alerts");
                }}
                testId="stark-ghost-stat-alerts"
              />
            </div>

            <div className="ml-auto flex items-center gap-1">
              <div className="flex border border-primary/30" role="group" aria-label={t("stark.ghost.size")}>
                {SIZES.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setSize(s)}
                    aria-pressed={size === s}
                    aria-label={t(`stark.ghost.size_${s}`)}
                    title={t(`stark.ghost.size_${s}`)}
                    className={cn("h-[30px] w-7 font-mono text-[11px] font-semibold uppercase", size === s ? "bg-primary/20 text-primary" : "text-muted-foreground hover:text-foreground")}
                    data-testid={`stark-ghost-size-${s}`}
                  >
                    {s}
                  </button>
                ))}
              </div>
              <ToolButton label={t("stark.ghost.view_wall")} onClick={() => setView("wall")} active={view === "wall"} testId="stark-ghost-view-wall">
                <LayoutGrid className="h-3.5 w-3.5" aria-hidden />
              </ToolButton>
              <ToolButton label={t("stark.ghost.view_map")} onClick={() => showMap(layer)} active={view === "map"} testId="stark-ghost-view-map">
                <MapIcon className="h-3.5 w-3.5" aria-hidden />
              </ToolButton>
              <ToolButton label={t(paused ? "stark.cctv.resume" : "stark.cctv.pause")} onClick={() => setPaused((p) => !p)} active={paused} testId="stark-ghost-pause">
                {paused ? <Play className="h-3.5 w-3.5" aria-hidden /> : <Pause className="h-3.5 w-3.5" aria-hidden />}
              </ToolButton>
              <ToolButton label={t("stark.ghost.details")} onClick={() => setDrawer((d) => (d ? null : "operators"))} active={!!drawer} testId="stark-ghost-drawer-toggle">
                <PanelRight className="h-3.5 w-3.5" aria-hidden />
              </ToolButton>
            </div>
          </div>
          {gevLine && (
            <p className="shrink-0 border-l-2 border-[hsl(var(--stark-hot))] pl-2 text-[11px] text-muted-foreground" data-testid="stark-ghost-gev">
              {gevLine}
            </p>
          )}

          {/* The wall or the map, with the drawer and the large view over them. */}
          <div className="relative flex min-h-0 flex-1 flex-col">
            {view === "wall" ? (
              <CameraWall
                cams={shown}
                gev={data?.gev ?? null}
                tick={wallTick}
                size={size}
                resetKey={resetKey}
                onOpen={setFocusId}
                empty={cams.length ? t("stark.cctv.none") : gevLine ?? t("stark.cctv.loading")}
              />
            ) : (
              <FusionMap
                markers={markers}
                selected={selectedMarker}
                onSelect={pickMarker}
                layer={layer}
                onLayer={setLayer}
                pick={mapPick}
                stations={stations}
                events={events}
                onClosePick={() => setMapPick(null)}
              />
            )}
            {drawer && (
              <Drawer
                tab={drawer}
                onTab={setDrawer}
                onClose={() => setDrawer(null)}
                shown={shown}
                cams={cams}
                deadCount={dead.size}
                gevUp={!!data?.available}
                liveUp={!!live.data}
                youtubeCount={videoCount}
                stationsUp={!!stationsQ.data}
                stationCount={stations.length}
                sources={sources}
                health={health.data}
                alerts={alerts}
                recent={recent}
                onOperator={(name) => {
                  setWords(name);
                  setCountry(null);
                  setCity(null);
                  setView("wall");
                }}
                onAlert={(id) => {
                  setMapPick({ kind: "incident", id });
                  setLayer((l) => (l === "cameras" || l === "stations" ? "incidents" : l));
                  setView("map");
                }}
              />
            )}
            {focusCam && (
              <FocusView
                cam={focusCam}
                index={Math.max(0, focusIndex)}
                total={focusIndex >= 0 ? shown.length : 1}
                gev={data?.gev ?? null}
                tick={focusTick}
                onStep={step}
                onNextCity={() => nextCity(1)}
                onRandom={randomPlace}
                onClose={() => setFocusId(null)}
              />
            )}
          </div>
        </div>
      </StarkShell>
    </MotionConfig>
  );
}

function ToolButton({ label, onClick, active, testId, children }: { label: string; onClick: () => void; active?: boolean; testId?: string; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      aria-pressed={active}
      data-testid={testId}
      className={cn(
        "flex h-8 w-8 shrink-0 items-center justify-center border transition-colors",
        active ? "border-primary/70 bg-primary/15 text-primary" : "border-primary/30 text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function Stat({
  icon: Icon,
  label,
  value,
  hint,
  tone,
  active,
  onClick,
  testId,
}: {
  icon: LucideIcon;
  label: string;
  value: ReactNode;
  hint: string;
  tone?: "hot";
  active?: boolean;
  onClick: () => void;
  testId: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={`${label} · ${hint}`}
      aria-pressed={active}
      data-testid={testId}
      className={cn(
        "flex h-8 items-center gap-1.5 border px-2 transition-colors",
        active ? "border-primary/70 bg-primary/15" : "border-primary/20 hover:border-primary/60 hover:bg-primary/10",
      )}
    >
      <Icon className={cn("h-3.5 w-3.5 shrink-0", tone === "hot" ? "text-[hsl(var(--stark-hot))]" : "text-primary")} aria-hidden />
      <span className={cn("font-hud text-sm font-semibold tabular-nums", tone === "hot" ? "text-[hsl(var(--stark-hot))]" : "text-foreground")}>{value}</span>
      <span className="font-hud text-[9px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">{label}</span>
    </button>
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

/** The fusion map at its own proportions, as large as the space allows. */
function FusionMap({
  markers,
  selected,
  onSelect,
  layer,
  onLayer,
  pick,
  stations,
  events,
  onClosePick,
}: {
  markers: MapMarker[];
  selected: string | null;
  onSelect: (id: string) => void;
  layer: Layer;
  onLayer: (l: Layer) => void;
  pick: MapPick | null;
  stations: StationNow[];
  events: IntelEvent[];
  onClosePick: () => void;
}) {
  const t = useT();
  return (
    <div className="stark-panel relative flex min-h-0 flex-1 flex-col gap-2 p-2" data-testid="stark-ghost-map">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <div className="flex flex-wrap gap-1" role="group" aria-label={t("stark.ghost.layers")}>
          {(["all", "cameras", "stations", "incidents"] as Layer[]).map((l) => (
            <Chip key={l} active={layer === l} onClick={() => onLayer(l)} testId={`stark-ghost-layer-${l}`}>
              {t(`stark.ghost.layer_${l}`)}
            </Chip>
          ))}
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-muted-foreground">
          <Legend tone="calm" label={t("stark.ghost.legend_cameras")} />
          <Legend tone="warn" label={t("stark.ghost.legend_stations")} />
          <Legend tone="hot" label={t("stark.ghost.legend_incidents")} />
        </div>
        <span className="text-[10px] text-muted-foreground">{t("stark.ghost.map_hint")}</span>
      </div>
      <div className="flex min-h-0 flex-1 items-center justify-center" style={{ containerType: "size" }}>
        <div style={{ width: "min(100cqw, calc(100cqh * 360 / 140))", aspectRatio: "360 / 140" }}>
          <WorldDotMap markers={markers} selected={selected} onSelect={onSelect} dotAlpha={0.45} className="h-full w-full" testId="stark-ghost-fusion" />
        </div>
      </div>
      {pick && <MapCard pick={pick} stations={stations} events={events} onClose={onClosePick} />}
    </div>
  );
}

function MapCard({ pick, stations, events, onClose }: { pick: MapPick; stations: StationNow[]; events: IntelEvent[]; onClose: () => void }) {
  const t = useT();
  let body: ReactNode = null;
  if (pick.kind === "station") {
    const s = stations.find((x) => x.station.norad_id === pick.id);
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
  } else {
    const e = events.find((x) => x.id === pick.id);
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
  if (!body) return null;
  return (
    <div className="stark-panel absolute bottom-3 left-3 z-10 w-[min(22rem,calc(100%-1.5rem))] bg-background/95 p-3">
      <button type="button" onClick={onClose} aria-label={t("stark.ghost.clear")} className="absolute right-2 top-2 text-muted-foreground hover:text-foreground" data-testid="stark-ghost-clear">
        <X className="h-3.5 w-3.5" aria-hidden />
      </button>
      {body}
    </div>
  );
}

function Bars({ rows, total, label, onPick }: { rows: [string, number][]; total: number; label: (key: string) => string; onPick?: (key: string) => void }) {
  return (
    <ul className="flex flex-col gap-1">
      {rows.map(([key, n]) => {
        const inner = (
          <>
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-foreground">{label(key)}</span>
              <span className="font-mono tabular-nums text-muted-foreground">{n.toLocaleString()}</span>
            </div>
            <div className="mt-0.5 h-1 bg-primary/10">
              <div className="h-full bg-primary/70" style={{ width: `${total ? Math.max(2, (n / total) * 100) : 0}%` }} />
            </div>
          </>
        );
        return (
          <li key={key} className="text-[11px]">
            {onPick ? (
              <button type="button" onClick={() => onPick(key)} className="block w-full px-1 py-0.5 text-left hover:bg-primary/10">
                {inner}
              </button>
            ) : (
              <div className="px-1 py-0.5">{inner}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Everything that is not a camera: out of the way until asked for. */
function Drawer({
  tab,
  onTab,
  onClose,
  shown,
  cams,
  deadCount,
  gevUp,
  liveUp,
  youtubeCount,
  stationsUp,
  stationCount,
  sources,
  health,
  alerts,
  recent,
  onOperator,
  onAlert,
}: {
  tab: DrawerTab;
  onTab: (tab: DrawerTab) => void;
  onClose: () => void;
  shown: GhostCam[];
  cams: GhostCam[];
  deadCount: number;
  gevUp: boolean;
  liveUp: boolean;
  youtubeCount: number;
  stationsUp: boolean;
  stationCount: number;
  sources: IntelSource[];
  health: { ok: number; degraded: number } | undefined;
  alerts: IntelEvent[];
  recent: IntelEvent[];
  onOperator: (name: string) => void;
  onAlert: (id: string) => void;
}) {
  const t = useT();
  const placedShown = useMemo(
    () => shown.filter((c): c is GhostCam & { lat: number; lon: number } => c.lat !== null && c.lon !== null),
    [shown],
  );
  const counts = useMemo(() => tally(placedShown), [placedShown]);
  const tabs: DrawerTab[] = ["operators", "regions", "health", "alerts"];
  const title: Record<DrawerTab, string> = {
    operators: t("stark.ghost.tab_operators"),
    regions: t("stark.ghost.tab_regions"),
    health: t("stark.ghost.tab_health"),
    alerts: t("stark.ghost.alerts"),
  };
  const heading: Record<DrawerTab, string> = {
    operators: t("stark.ghost.by_operator"),
    regions: t("stark.ghost.by_region"),
    health: t("stark.ghost.feed_health"),
    alerts: t("stark.ghost.alerts"),
  };
  return (
    <aside className="stark-panel absolute bottom-0 right-0 top-0 z-20 flex w-[min(24rem,100%)] flex-col bg-background/95 backdrop-blur" data-testid="stark-ghost-drawer">
      <div className="flex items-center gap-1 border-b border-primary/15 p-2" role="tablist">
        {tabs.map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            onClick={() => onTab(id)}
            className={cn(
              "min-w-0 flex-1 truncate px-1.5 py-1 font-mono text-[9px] font-semibold uppercase tracking-[0.12em]",
              tab === id ? "bg-primary/20 text-primary" : "text-muted-foreground hover:text-foreground",
            )}
            data-testid={`stark-ghost-tab-${id}`}
          >
            {title[id]}
          </button>
        ))}
        <button type="button" onClick={onClose} aria-label={t("stark.ghost.clear")} className="ml-1 text-muted-foreground hover:text-foreground" data-testid="stark-ghost-drawer-close">
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3" role="tabpanel" data-testid={`stark-ghost-panel-${tab}`}>
        <h3 className="mb-2 font-hud text-[10px] font-semibold uppercase tracking-[0.2em] text-foreground/90">{heading[tab]}</h3>
        {tab === "operators" && (
          <>
            <p className="mb-2 text-[10px] text-muted-foreground">{t("stark.ghost.operator_hint")}</p>
            <Bars rows={counts.providers.slice(0, 40)} total={shown.length} label={(k) => k} onPick={onOperator} />
          </>
        )}
        {tab === "regions" && (
          <Bars
            rows={REGIONS.map((r) => [r, counts.regions.find(([k]) => k === r)?.[1] ?? 0] as [string, number]).filter(([, n]) => n > 0)}
            total={placedShown.length}
            label={(k) => t(`stark.region.${k}`)}
          />
        )}
        {tab === "health" && (
          <ul className="flex flex-col gap-1.5 text-[11px]">
            <HealthRow state={gevUp ? "on" : "off"} name="God’s Eye View" value={gevUp ? (cams.length - youtubeCount).toLocaleString() : t("stark.ghost.down")} />
            {health && (
              <li className="pl-4 text-[10px] text-muted-foreground">{fill(t("stark.ghost.frames_state"), { ok: health.ok, slow: health.degraded })}</li>
            )}
            {deadCount > 0 && <li className="pl-4 text-[10px] text-muted-foreground">{fill(t("stark.ghost.dead_hidden"), { count: deadCount })}</li>}
            <HealthRow state={liveUp ? "on" : "off"} name={t("stark.ghost.youtube")} value={liveUp ? String(youtubeCount) : "—"} />
            <HealthRow state={stationsUp ? "on" : "off"} name={t("stark.ghost.celestrak")} value={stationCount ? String(stationCount) : "—"} />
            {sources.map((s) => (
              <HealthRow key={s.id} state={sourceLamp(s.status)} name={s.name} value={String(s.count)} />
            ))}
          </ul>
        )}
        {tab === "alerts" && (
          <div className="flex flex-col gap-3">
            {alerts.length ? (
              <ul className="flex flex-col gap-1">
                {alerts.slice(0, 60).map((e) => (
                  <li key={e.id}>
                    <button type="button" onClick={() => onAlert(e.id)} className="block w-full px-1 py-1 text-left text-[11px] hover:bg-primary/10" data-testid="stark-ghost-alert">
                      <span className="block truncate font-semibold text-foreground">{e.title}</span>
                      <span className="block truncate text-[10px] text-muted-foreground">
                        {[e.place, e.severity_label, new Date(lastSeen(e)).toLocaleString()].filter(Boolean).join(" · ")}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[11px] text-muted-foreground">{t("stark.ghost.no_alerts")}</p>
            )}
            <div>
              <p className="mb-1 font-mono text-[9px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                {t("stark.ghost.pulse")} · {t("stark.ghost.last_24h")}
              </p>
              <Bars rows={kindCounts(recent).slice(0, 8)} total={recent.length} label={(k) => k.replace(/_/g, " ")} />
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}

function HealthRow({ state, name, value }: { state: "on" | "warn" | "off"; name: string; value: string }) {
  return (
    <li className="flex items-center gap-2">
      <StarkLamp state={state} />
      <span className="flex-1 truncate text-foreground">{name}</span>
      <span className="font-mono tabular-nums text-muted-foreground">{value}</span>
    </li>
  );
}
