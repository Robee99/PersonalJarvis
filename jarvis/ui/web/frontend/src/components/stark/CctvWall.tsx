import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Pause, Play, Search, X } from "lucide-react";
import { StarkLamp, StarkPanel } from "@/components/stark/StarkPanel";
import { IconButton } from "@/components/stark/LivePanels";
import { fill, useT } from "@/i18n";
import {
  FOCUS_REFRESH_MS,
  frameUrl,
  useCctvCatalog,
  WALL_REFRESH_MS,
  type CctvCamera,
  type CctvCatalog,
} from "@/lib/cctv";
import { cn } from "@/lib/utils";

/**
 * GOD'S EYE VIEW CAMERAS: every public road and city camera God's Eye View
 * lists, by city, as a wall of frames that load again every few seconds.
 * One camera can be opened large, where it updates faster. Frames load only
 * while the window is visible and the wall is not paused.
 */

const PER_PAGE = 9;

/** A counter that goes up every `ms` while the page is visible and `on`. */
export function useFrameTick(ms: number, on = true): number {
  const [tick, setTick] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    const id = window.setInterval(() => {
      if (document.visibilityState !== "hidden") setTick(Date.now());
    }, ms);
    return () => window.clearInterval(id);
  }, [ms, on]);
  return tick;
}

/** The cameras of a catalog the wall can show, filtered by city and words. */
export function wallCameras(data: CctvCatalog | undefined, city: string | null, words: string): CctvCamera[] {
  const wanted = words.trim().toLowerCase();
  return (data?.cameras ?? []).filter(
    (c) =>
      c.feed === "image" &&
      (!city || c.city_id === city) &&
      (!wanted || `${c.name} ${c.city} ${c.provider}`.toLowerCase().includes(wanted)),
  );
}

/** One camera frame; says so when the frame did not load. */
export function CctvFrame({
  gev,
  camera,
  tick,
  className,
  onOpen,
}: {
  gev: string;
  camera: CctvCamera;
  tick: number;
  className?: string;
  onOpen?: () => void;
}) {
  const t = useT();
  const [failed, setFailed] = useState(false);
  const src = frameUrl(gev, camera.id, tick);
  useEffect(() => setFailed(false), [src]);
  const body = (
    <>
      {failed ? (
        <span className="absolute inset-0 flex items-center justify-center p-2 text-center text-[10px] text-muted-foreground">
          {t("stark.cctv.no_frame")}
        </span>
      ) : (
        <img
          src={src}
          alt={fill(t("stark.cctv.frame_alt"), { camera: camera.name, city: camera.city })}
          className="absolute inset-0 h-full w-full object-cover"
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
          data-testid="stark-cctv-frame"
          data-camera={camera.id}
        />
      )}
      <span className="pointer-events-none absolute left-1 top-1 flex max-w-[calc(100%-0.5rem)] items-center gap-1 bg-background/80 px-1.5 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-[0.12em] text-foreground">
        <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-[hsl(var(--stark-hot))]" />
        <span className="truncate">{camera.name}</span>
      </span>
      <span className="pointer-events-none absolute bottom-1 right-1 bg-background/70 px-1 font-mono text-[8px] tabular-nums text-muted-foreground">
        {camera.lat.toFixed(3)}, {camera.lon.toFixed(3)}
      </span>
    </>
  );
  const box = cn("relative block min-h-0 min-w-0 overflow-hidden border border-primary/15 bg-black", className);
  return onOpen ? (
    <button type="button" onClick={onOpen} className={cn(box, "text-left hover:border-primary/50")} aria-label={fill(t("stark.cctv.open"), { camera: camera.name })}>
      {body}
    </button>
  ) : (
    <div className={box}>{body}</div>
  );
}

export function CctvWall({ className }: { className?: string }) {
  const t = useT();
  const q = useCctvCatalog();
  const data = q.data;
  const [city, setCity] = useState<string | null>(null);
  const [words, setWords] = useState("");
  const [page, setPage] = useState(0);
  const [focus, setFocus] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const tick = useFrameTick(WALL_REFRESH_MS, !paused && !focus);
  const focusTick = useFrameTick(FOCUS_REFRESH_MS, !paused && !!focus);

  const cities = data?.cities ?? [];
  const cityId = city ?? cities.find((c) => c.count > c.video)?.id ?? null;
  const shown = useMemo(() => wallCameras(data, words ? null : cityId, words), [data, cityId, words]);
  const pages = Math.max(1, Math.ceil(shown.length / PER_PAGE));
  const at = Math.min(page, pages - 1);
  const onPage = shown.slice(at * PER_PAGE, at * PER_PAGE + PER_PAGE);
  const big = focus ? data?.cameras.find((c) => c.id === focus) : undefined;
  const total = data?.cameras.length ?? 0;
  const video = data?.cameras.filter((c) => c.feed === "video").length ?? 0;

  return (
    <StarkPanel
      title={t("stark.cctv.title")}
      meta={
        data?.available ? (
          <span className="flex items-center gap-1.5">
            <StarkLamp state={paused ? "off" : "on"} />
            {fill(t("stark.cctv.count"), { count: total.toLocaleString(), cities: cities.length })}
          </span>
        ) : undefined
      }
      action={
        data?.available ? (
          <IconButton label={t(paused ? "stark.cctv.resume" : "stark.cctv.pause")} onClick={() => setPaused((p) => !p)} active={paused} testId="stark-cctv-pause">
            {paused ? <Play className="h-3 w-3" aria-hidden /> : <Pause className="h-3 w-3" aria-hidden />}
          </IconButton>
        ) : undefined
      }
      className={className}
      bodyClassName="flex min-h-0 gap-3"
      testId="stark-cctv"
    >
      {!data ? (
        <p className="text-xs text-muted-foreground">{t(q.isError ? "stark.cctv.error" : "stark.cctv.loading")}</p>
      ) : !data.available || !data.gev ? (
        <p className="max-w-prose text-xs text-muted-foreground" data-testid="stark-cctv-down">
          {t(data.reason === "not_loopback" ? "stark.cctv.not_local" : "stark.cctv.gev_down")}
        </p>
      ) : (
        <>
          <div className="flex w-44 shrink-0 flex-col gap-2 xl:w-52">
            <label className="flex items-center gap-1.5 border border-primary/20 px-2 py-1">
              <Search className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
              <input
                value={words}
                onChange={(e) => {
                  setWords(e.target.value);
                  setPage(0);
                }}
                placeholder={t("stark.cctv.search")}
                aria-label={t("stark.cctv.search")}
                className="min-w-0 flex-1 bg-transparent text-[11px] text-foreground outline-none placeholder:text-muted-foreground"
                data-testid="stark-cctv-search"
              />
              {words && (
                <button type="button" onClick={() => setWords("")} aria-label={t("stark.cctv.clear")} className="text-muted-foreground hover:text-foreground">
                  <X className="h-3 w-3" aria-hidden />
                </button>
              )}
            </label>
            <ul className="min-h-0 flex-1 overflow-y-auto pr-1" aria-label={t("stark.cctv.cities")}>
              {cities.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setCity(c.id);
                      setWords("");
                      setPage(0);
                      setFocus(null);
                    }}
                    aria-pressed={!words && c.id === cityId}
                    data-testid={`stark-cctv-city-${c.id}`}
                    className={cn(
                      "flex w-full items-center gap-2 px-1.5 py-1 text-left text-[11px] transition-colors",
                      !words && c.id === cityId ? "bg-primary/15 text-primary" : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    <span className="min-w-0 flex-1 truncate">{c.name}</span>
                    <span className="font-mono text-[10px] tabular-nums">{c.count - c.video}</span>
                  </button>
                </li>
              ))}
            </ul>
            {video > 0 && <p className="text-[10px] leading-snug text-muted-foreground">{fill(t("stark.cctv.video_note"), { count: video })}</p>}
          </div>
          <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-1.5">
            {big ? (
              <div className="flex min-h-0 flex-1 flex-col gap-1.5" data-testid="stark-cctv-focus">
                <CctvFrame gev={data.gev} camera={big} tick={focusTick} className="flex-1" />
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
                  <span className="font-semibold text-foreground">{big.name}</span>
                  <span>{big.city}</span>
                  <span>{big.provider}</span>
                  {big.heading !== null && <span>{fill(t("stark.cctv.heading"), { deg: Math.round(big.heading) })}</span>}
                  <button type="button" onClick={() => setFocus(null)} className="ml-auto font-mono uppercase tracking-[0.14em] text-primary hover:underline" data-testid="stark-cctv-back">
                    {t("stark.cctv.back")}
                  </button>
                </div>
              </div>
            ) : onPage.length ? (
              <>
                <div className="grid min-h-0 flex-1 grid-cols-3 grid-rows-3 gap-1">
                  {onPage.map((cam) => (
                    <CctvFrame key={cam.id} gev={data.gev!} camera={cam} tick={tick} onOpen={() => setFocus(cam.id)} />
                  ))}
                </div>
                <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                  <span className="truncate">{onPage[0]?.provider}</span>
                  {pages > 1 && (
                    <span className="ml-auto flex items-center gap-1 font-mono tabular-nums">
                      <button type="button" onClick={() => setPage(Math.max(0, at - 1))} disabled={at === 0} aria-label={t("stark.cctv.prev")} className="text-primary disabled:opacity-30" data-testid="stark-cctv-prev">
                        <ChevronLeft className="h-3 w-3" aria-hidden />
                      </button>
                      {at + 1}/{pages}
                      <button type="button" onClick={() => setPage(Math.min(pages - 1, at + 1))} disabled={at >= pages - 1} aria-label={t("stark.cctv.next")} className="text-primary disabled:opacity-30" data-testid="stark-cctv-next">
                        <ChevronRight className="h-3 w-3" aria-hidden />
                      </button>
                    </span>
                  )}
                </div>
              </>
            ) : (
              <p className="text-xs text-muted-foreground">{t("stark.cctv.none")}</p>
            )}
          </div>
        </>
      )}
    </StarkPanel>
  );
}
