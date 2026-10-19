import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ChevronLeft, ChevronRight, ExternalLink, Play, Search, Shuffle, SkipForward, Video, X } from "lucide-react";
import { CctvFrame } from "@/components/stark/CctvWall";
import { YouTubeFrame } from "@/components/stark/LivePanels";
import { WorldDotMap } from "@/components/stark/WorldDotMap";
import { fill, useT } from "@/i18n";
import { playsChannel, posterUrl, useNowPlaying, type LiveStream } from "@/lib/live";
import { gridWindow, matchPlaces, TILE_GAP, TILE_MIN, type GhostCam, type GhostCity, type GhostCountry, type TileSize } from "@/lib/ghostWall";
import { cn } from "@/lib/utils";

/**
 * The Ghost screen's camera wall: a grid of every public camera in the
 * chosen place that draws only the rows on screen and loads a row's frames
 * once scrolling stops, the place picker, and the focus view that shows one
 * camera over most of the screen.
 */

/** How long the wall waits after scrolling stops before it loads frames. */
const SETTLE_MS = 180;

export function countryLabel(country: Pick<GhostCountry, "name">, other: string): string {
  return country.name || other;
}

/** A country's two-letter code as a small badge (flag emoji do not draw on every system). */
export function CodeBadge({ code }: { code: string }) {
  return (
    <span className="inline-flex w-6 shrink-0 justify-center border border-primary/25 font-mono text-[9px] font-semibold uppercase leading-4 text-primary/90">
      {code || "··"}
    </span>
  );
}

/** The grid. `resetKey` changing scrolls back to the top (a new place was picked). */
export function CameraWall({
  cams,
  gev,
  tick,
  size,
  resetKey,
  onOpen,
  empty,
}: {
  cams: GhostCam[];
  gev: string | null;
  tick: number;
  size: TileSize;
  resetKey: string;
  onOpen: (id: string) => void;
  empty: ReactNode;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [top, setTop] = useState(0);
  const [settled, setSettled] = useState(0);
  const settleTimer = useRef<number | null>(null);

  useLayoutEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const measure = () => setBox({ w: el.clientWidth, h: el.clientHeight });
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const el = boxRef.current;
    if (el) el.scrollTop = 0;
    setTop(0);
    setSettled(0);
  }, [resetKey]);

  useEffect(
    () => () => {
      if (settleTimer.current !== null) window.clearTimeout(settleTimer.current);
    },
    [],
  );

  const onScroll = () => {
    const el = boxRef.current;
    if (!el) return;
    setTop(el.scrollTop);
    if (settleTimer.current !== null) window.clearTimeout(settleTimer.current);
    settleTimer.current = window.setTimeout(() => {
      settleTimer.current = null;
      setSettled(el.scrollTop);
    }, SETTLE_MS);
  };

  // Before the first measurement (and in tests) assume a common window size.
  const width = box.w || 1280;
  const height = box.h || 720;
  const win = gridWindow({ width, height, scrollTop: top, count: cams.length, minTile: TILE_MIN[size] });
  const loadWin = gridWindow({ width, height, scrollTop: settled, count: cams.length, minTile: TILE_MIN[size], overscan: 0 });

  return (
    <div
      ref={boxRef}
      onScroll={onScroll}
      className="relative min-h-0 flex-1 overflow-y-auto overflow-x-hidden"
      data-testid="stark-ghost-wall"
      data-count={cams.length}
      data-cols={win.cols}
    >
      {cams.length === 0 ? (
        <div className="flex h-full items-center justify-center p-6 text-center text-xs text-muted-foreground">{empty}</div>
      ) : (
        <div className="relative w-full" style={{ height: win.totalH }}>
          {cams.slice(win.first, win.last).map((cam, i) => {
            const index = win.first + i;
            const row = Math.floor(index / win.cols);
            const col = index % win.cols;
            const load = index >= loadWin.first && index < loadWin.last;
            return (
              <div
                key={cam.id}
                className="absolute"
                style={{ top: row * win.rowH, left: col * (win.tileW + TILE_GAP), width: win.tileW, height: win.tileH }}
                data-testid="stark-ghost-tile"
                data-camera={cam.id}
              >
                <WallTile cam={cam} gev={gev} tick={tick} load={load} small={size === "s"} onOpen={() => onOpen(cam.id)} />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function TileCaption({ cam, small }: { cam: GhostCam; small: boolean }) {
  return (
    <span className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end gap-1 bg-gradient-to-t from-black/85 via-black/45 to-transparent px-1.5 pb-1 pt-4 text-left">
      <span className="min-w-0 flex-1">
        <span className="block truncate font-mono text-[9px] font-semibold uppercase tracking-[0.1em] text-white">{cam.name}</span>
        {!small && (
          <span className="block truncate text-[10px] text-white/70">
            {cam.city}
            {cam.country && cam.country !== cam.city ? ` · ${cam.country}` : ""}
          </span>
        )}
      </span>
      {cam.countryCode && <span className="shrink-0 font-mono text-[9px] font-semibold text-white/80">{cam.countryCode}</span>}
    </span>
  );
}

function WallTile({ cam, gev, tick, load, small, onOpen }: { cam: GhostCam; gev: string | null; tick: number; load: boolean; small: boolean; onOpen: () => void }) {
  if (cam.kind === "video" && cam.stream) return <VideoTile cam={cam} stream={cam.stream} load={load} small={small} onOpen={onOpen} />;
  if (!cam.camera || !gev) return null;
  return (
    <CctvFrame gev={gev} camera={cam.camera} tick={tick} load={load} label={false} coords={false} onOpen={onOpen} className="h-full w-full">
      <TileCaption cam={cam} small={small} />
    </CctvFrame>
  );
}

/** A YouTube camera on the wall: its still, a video mark, and it plays once opened. */
function VideoTile({ cam, stream, load, small, onOpen }: { cam: GhostCam; stream: LiveStream; load: boolean; small: boolean; onOpen: () => void }) {
  const t = useT();
  const now = useNowPlaying(stream, load);
  const video = playsChannel(stream) ? now.data : stream.embed;
  const [failed, setFailed] = useState(false);
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={fill(t("stark.cctv.open"), { camera: cam.name })}
      className="relative block h-full w-full overflow-hidden border border-[hsl(var(--stark-hot)/0.35)] bg-black text-left hover:border-[hsl(var(--stark-hot)/0.8)] focus-visible:border-primary focus-visible:outline-none"
      data-testid="stark-ghost-video-tile"
    >
      {load && video && !failed && (
        <img src={posterUrl(video)} alt="" className="absolute inset-0 h-full w-full object-cover opacity-80" loading="lazy" decoding="async" onError={() => setFailed(true)} />
      )}
      <span className="absolute left-1 top-1 flex items-center gap-1 bg-[hsl(var(--stark-hot)/0.9)] px-1.5 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-[0.12em] text-black">
        <Video className="h-2.5 w-2.5" aria-hidden />
        {t("stark.ghost.video_badge")}
      </span>
      <span aria-hidden className="absolute left-1/2 top-1/2 flex h-9 w-9 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-white/60 bg-black/50 text-white">
        <Play className="h-4 w-4" />
      </span>
      <TileCaption cam={cam} small={small} />
    </button>
  );
}

/**
 * The place picker: countries on the left, the chosen country's cities on
 * the right, and one search box over both. A country fills the wall the
 * moment it is clicked; a city does and closes the picker.
 */
export function PlacePicker({
  places,
  country,
  city,
  onCountry,
  onCity,
  onClose,
}: {
  places: GhostCountry[];
  country: string | null;
  city: string | null;
  onCountry: (key: string | null) => void;
  onCity: (city: GhostCity) => void;
  onClose: () => void;
}) {
  const t = useT();
  const [q, setQ] = useState("");
  const [browse, setBrowse] = useState<string | null>(country);
  const ref = useRef<HTMLDivElement>(null);
  const other = t("stark.ghost.unknown_country");
  const found = useMemo(() => matchPlaces(places, q), [places, q]);
  const shownCountry = places.find((c) => c.key === browse) ?? null;
  const total = places.reduce((n, c) => n + c.count, 0);

  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const pickCountry = (key: string | null) => {
    setBrowse(key);
    onCountry(key);
  };
  const cityRows = q.trim() ? found.cities : (shownCountry?.cities ?? []).map((c) => ({ ...c, country: shownCountry! }));

  return (
    <div
      ref={ref}
      role="dialog"
      aria-label={t("stark.ghost.place_pick")}
      className="stark-panel absolute left-0 top-full z-40 mt-1 flex h-[min(30rem,70vh)] w-[min(44rem,calc(100vw-2rem))] flex-col gap-2 bg-background/95 p-2 backdrop-blur"
      data-testid="stark-ghost-picker"
    >
      <label className="flex items-center gap-1.5 border border-primary/25 px-2 py-1">
        <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <input
          autoFocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter") return;
            if (found.cities[0]) onCity(found.cities[0]);
            else if (found.countries[0]) pickCountry(found.countries[0].key);
          }}
          placeholder={t("stark.ghost.place_search")}
          aria-label={t("stark.ghost.place_search")}
          className="min-w-0 flex-1 bg-transparent text-xs text-foreground outline-none placeholder:text-muted-foreground"
          data-testid="stark-ghost-picker-search"
        />
      </label>
      <div className="grid min-h-0 flex-1 grid-cols-2 gap-2">
        <ul className="min-h-0 overflow-y-auto pr-1" aria-label={t("stark.ghost.countries")}>
          {!q.trim() && (
            <PlaceRow active={!country} onClick={() => pickCountry(null)} label={t("stark.ghost.all_countries")} count={total} testId="stark-ghost-country-all" />
          )}
          {found.countries.map((c) => (
            <PlaceRow
              key={c.key}
              active={c.key === country}
              browsing={c.key === browse && c.key !== country}
              onClick={() => pickCountry(c.key)}
              onHover={() => setBrowse(c.key)}
              label={countryLabel(c, other)}
              code={c.code}
              count={c.count}
              sub={fill(t(c.cities.length === 1 ? "stark.ghost.city_count_one" : "stark.ghost.city_count"), { count: c.cities.length })}
              testId={`stark-ghost-country-${c.key}`}
            />
          ))}
          {q.trim() && !found.countries.length && <li className="px-1.5 py-1 text-[11px] text-muted-foreground">{t("stark.ghost.no_country")}</li>}
        </ul>
        <ul className="min-h-0 overflow-y-auto border-l border-primary/15 pl-2 pr-1" aria-label={t("stark.ghost.cities")}>
          <li className="px-1.5 pb-1 font-mono text-[9px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
            {q.trim() ? t("stark.ghost.cities") : shownCountry ? fill(t("stark.ghost.cities_in"), { country: countryLabel(shownCountry, other) }) : t("stark.ghost.pick_country")}
          </li>
          {cityRows.map((c) => (
            <PlaceRow
              key={c.key}
              active={c.key === city}
              onClick={() => onCity(c)}
              label={c.name}
              sub={q.trim() ? countryLabel(c.country, other) : undefined}
              code={q.trim() ? c.country.code : undefined}
              count={c.count}
              testId={`stark-ghost-city-${c.key}`}
            />
          ))}
          {q.trim() && !cityRows.length && <li className="px-1.5 py-1 text-[11px] text-muted-foreground">{t("stark.ghost.no_city")}</li>}
        </ul>
      </div>
    </div>
  );
}

function PlaceRow({
  active,
  browsing,
  onClick,
  onHover,
  label,
  sub,
  code,
  count,
  testId,
}: {
  active: boolean;
  browsing?: boolean;
  onClick: () => void;
  onHover?: () => void;
  label: string;
  sub?: string;
  code?: string;
  count: number;
  testId?: string;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        onMouseEnter={onHover}
        onFocus={onHover}
        aria-pressed={active}
        data-testid={testId}
        className={cn(
          "flex w-full items-center gap-2 px-1.5 py-1 text-left text-xs transition-colors",
          active ? "bg-primary/20 text-primary" : browsing ? "bg-primary/10 text-foreground" : "text-foreground/85 hover:bg-primary/10 hover:text-foreground",
        )}
      >
        {code !== undefined && <CodeBadge code={code} />}
        <span className="min-w-0 flex-1 truncate">
          {label}
          {sub && <span className="ml-1.5 text-[10px] text-muted-foreground">{sub}</span>}
        </span>
        <span className="font-mono text-[10px] tabular-nums text-muted-foreground">{count.toLocaleString()}</span>
      </button>
    </li>
  );
}

/** A small dotted map with one pin, kept at the map's own proportions. */
export function PinMap({ lat, lon, label }: { lat: number; lon: number; label: string }) {
  return (
    <div className="w-full border border-primary/15 bg-background/40" style={{ aspectRatio: "360 / 140" }}>
      <WorldDotMap markers={[{ id: "pin", lat, lon, tone: "hot", weight: 0.6, label }]} selected="pin" dotAlpha={0.4} className="h-full w-full" testId="stark-ghost-pin" />
    </div>
  );
}

/**
 * One camera over most of the screen: the frame (or the YouTube player,
 * muted), where it is, who runs it, a pin on the map, and the controls to
 * step through the cameras of the current place or jump somewhere else.
 */
export function FocusView({
  cam,
  index,
  total,
  gev,
  tick,
  onStep,
  onNextCity,
  onRandom,
  onClose,
}: {
  cam: GhostCam;
  index: number;
  total: number;
  gev: string | null;
  tick: number;
  onStep: (dir: 1 | -1) => void;
  onNextCity: () => void;
  onRandom: () => void;
  onClose: () => void;
}) {
  const t = useT();
  const other = t("stark.ghost.unknown_country");
  return (
    <div
      className="absolute inset-0 z-30 flex flex-col gap-2 bg-background/95 p-2 backdrop-blur-sm lg:flex-row"
      role="dialog"
      aria-modal="true"
      aria-label={cam.name}
      data-testid="stark-ghost-focus"
      data-camera={cam.id}
    >
      <div className="relative min-h-[16rem] min-w-0 flex-1">
        {cam.kind === "video" && cam.stream ? (
          <YouTubeFrame stream={cam.stream} autoplay className="absolute inset-0" />
        ) : cam.camera && gev ? (
          <CctvFrame gev={gev} camera={cam.camera} tick={tick} fit="contain" label={false} className="absolute inset-0 h-full w-full" />
        ) : null}
        {total > 1 && (
          <>
            <button
              type="button"
              onClick={() => onStep(-1)}
              aria-label={t("stark.cctv.prev")}
              className="absolute left-2 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-full border border-primary/40 bg-background/70 text-primary hover:bg-primary/20"
              data-testid="stark-ghost-prev"
            >
              <ChevronLeft className="h-5 w-5" aria-hidden />
            </button>
            <button
              type="button"
              onClick={() => onStep(1)}
              aria-label={t("stark.cctv.next")}
              className="absolute right-2 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-full border border-primary/40 bg-background/70 text-primary hover:bg-primary/20"
              data-testid="stark-ghost-next"
            >
              <ChevronRight className="h-5 w-5" aria-hidden />
            </button>
          </>
        )}
      </div>
      <aside className="stark-panel flex shrink-0 flex-col gap-3 overflow-y-auto overflow-x-hidden p-3 lg:w-[20rem]">
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground" data-testid="stark-ghost-focus-index">
              {index + 1} / {total.toLocaleString()}
            </p>
            <h2 className="mt-0.5 break-words text-sm font-semibold text-foreground">{cam.name}</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("stark.cctv.back")}
            title={t("stark.cctv.back")}
            className="flex h-8 w-8 shrink-0 items-center justify-center border border-primary/30 text-muted-foreground hover:text-foreground"
            data-testid="stark-ghost-close"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px]">
          <Row k={t("stark.ghost.f_city")} v={cam.city} />
          <Row k={t("stark.ghost.f_country")} v={cam.country || other} />
          <Row k={t("stark.ghost.f_operator")} v={cam.provider || "—"} />
          {cam.lat !== null && cam.lon !== null && <Row k={t("stark.ghost.f_position")} v={`${cam.lat.toFixed(4)}, ${cam.lon.toFixed(4)}`} />}
          {cam.heading !== null && <Row k={t("stark.ghost.f_facing")} v={`${Math.round(cam.heading)}°`} />}
          <Row k={t("stark.ghost.f_feed")} v={t(cam.kind === "video" ? "stark.ghost.f_feed_video" : "stark.ghost.f_refresh_v")} />
        </dl>
        {cam.lat !== null && cam.lon !== null && <PinMap lat={cam.lat} lon={cam.lon} label={`${cam.city}${cam.country ? `, ${cam.country}` : ""}`} />}
        <div className="grid grid-cols-2 gap-1.5">
          <FocusButton onClick={() => onStep(-1)} disabled={total < 2} testId="stark-ghost-prev-cam">
            <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
            {t("stark.cctv.prev")}
          </FocusButton>
          <FocusButton onClick={() => onStep(1)} disabled={total < 2} testId="stark-ghost-next-cam">
            {t("stark.cctv.next")}
            <ChevronRight className="h-3.5 w-3.5" aria-hidden />
          </FocusButton>
          <FocusButton onClick={onNextCity} testId="stark-ghost-focus-next-city">
            <SkipForward className="h-3.5 w-3.5" aria-hidden />
            {t("stark.ghost.next_city")}
          </FocusButton>
          <FocusButton onClick={onRandom} testId="stark-ghost-focus-random">
            <Shuffle className="h-3.5 w-3.5" aria-hidden />
            {t("stark.ghost.random")}
          </FocusButton>
        </div>
        {cam.stream && (
          <a href={cam.stream.page} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline">
            {t("stark.live.open_youtube")}
            <ExternalLink className="h-3 w-3" aria-hidden />
          </a>
        )}
        <p className="mt-auto text-[10px] text-muted-foreground">{t("stark.ghost.keys_hint")}</p>
      </aside>
    </div>
  );
}

function FocusButton({ onClick, disabled, testId, children }: { onClick: () => void; disabled?: boolean; testId?: string; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
      className="flex items-center justify-center gap-1 border border-primary/30 px-2 py-1.5 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-primary hover:bg-primary/15 disabled:opacity-30"
    >
      {children}
    </button>
  );
}

export function Row({ k, v }: { k: string; v: string }) {
  return (
    <>
      <dt className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">{k}</dt>
      <dd className="min-w-0 truncate text-foreground" title={v}>
        {v}
      </dd>
    </>
  );
}
