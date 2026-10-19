import { useEffect, useRef, useState } from "react";
import { ChevronRight, ExternalLink, Grid2x2, LayoutGrid, Maximize2, Pause, Play, Square } from "lucide-react";
import { StarkLamp, StarkPanel } from "@/components/stark/StarkPanel";
import { fill, useT } from "@/i18n";
import { embedUrl, isPlayerError, playsChannel, useLiveCatalog, useNowPlaying, YOUTUBE_ORIGIN, type LiveStream } from "@/lib/live";
import { cn } from "@/lib/utils";
import { useIntelStore } from "@/store/intel";

/**
 * The Live screen's video: news channels (one large, or four at once) and a
 * grid of public city cameras by region. Both play in YouTube's embedded
 * player and start muted. The assistant can switch either one
 * (`world_globe` "watch").
 */

const CAMS_PER_PAGE = 4;
const WALL_SIZE = 4;
const PLAY_ALL_KEY = "world.live.playAll";

function savedPlayAll(): boolean {
  try {
    return window.localStorage.getItem(PLAY_ALL_KEY) === "1";
  } catch {
    // Storage can be blocked; cameras then wait for a click, the cheaper default.
    return false;
  }
}

function savePlayAll(on: boolean) {
  try {
    window.localStorage.setItem(PLAY_ALL_KEY, on ? "1" : "0");
  } catch {
    // Storage can be blocked; the choice then lasts until the screen closes.
  }
}

/** One YouTube player that notices when its stream cannot play. */
export function YouTubeFrame({ stream, autoplay, className }: { stream: LiveStream; autoplay: boolean; className?: string }) {
  const t = useT();
  const ref = useRef<HTMLIFrameElement>(null);
  const [offline, setOffline] = useState(false);
  const now = useNowPlaying(stream);
  // Hold the player back while the channel's current video is looked up, so
  // it does not load the channel embed first and then jump.
  const resolving = playsChannel(stream) && now.status === "pending" && now.fetchStatus === "fetching";
  const src = embedUrl(stream, autoplay, window.location.origin, now.data);

  useEffect(() => {
    setOffline(false);
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== YOUTUBE_ORIGIN || event.source !== ref.current?.contentWindow) return;
      if (isPlayerError(event.data)) setOffline(true);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [src]);

  // The player reports errors only to a page that said it is listening.
  const listen = () =>
    ref.current?.contentWindow?.postMessage(JSON.stringify({ event: "listening", id: stream.key, channel: "widget" }), YOUTUBE_ORIGIN);

  return (
    <div className={cn("relative min-h-0 min-w-0 overflow-hidden bg-black", className)}>
      {!resolving && <iframe
        key={src}
        ref={ref}
        src={src}
        title={stream.place ? `${stream.name} · ${stream.place}` : stream.name}
        onLoad={listen}
        className="absolute inset-0 h-full w-full border-0"
        allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
        allowFullScreen
        referrerPolicy="strict-origin-when-cross-origin"
        data-testid="stark-live-frame"
        data-stream={stream.key}
      />}
      {offline && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-background/90 p-3 text-center" data-testid="stark-live-offline">
          <p className="text-xs text-foreground">{fill(t("stark.live.offline"), { stream: stream.name })}</p>
          <a href={stream.page} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-primary hover:underline">
            {t("stark.live.open_youtube")}
            <ExternalLink className="h-3 w-3" aria-hidden />
          </a>
        </div>
      )}
    </div>
  );
}

export function Chip({ active, onClick, children, testId }: { active: boolean; onClick: () => void; children: React.ReactNode; testId?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      data-testid={testId}
      className={cn(
        "shrink-0 border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] transition-colors",
        active ? "border-[hsl(var(--stark-hot)/0.7)] bg-[hsl(var(--stark-hot)/0.18)] text-[hsl(var(--stark-hot))]" : "border-primary/20 text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

export function IconButton({ label, onClick, active, children, testId }: { label: string; onClick: () => void; active?: boolean; children: React.ReactNode; testId?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      aria-pressed={active}
      data-testid={testId}
      className={cn("flex h-6 w-6 items-center justify-center border transition-colors", active ? "border-primary/60 bg-primary/15 text-primary" : "border-primary/20 text-muted-foreground hover:text-foreground")}
    >
      {children}
    </button>
  );
}

/**
 * LIVE NEWS: one channel large, or a wall of four. Channels come in groups
 * (world, India); picking a channel starts it, and on the wall it takes the
 * place of the oldest pick.
 */
export function LiveNews({ className }: { className?: string }) {
  const t = useT();
  const q = useLiveCatalog();
  const news = (q.data?.streams ?? []).filter((s) => s.kind === "news");
  const groups = q.data?.news_groups ?? [];
  const [group, setGroup] = useState<string | null>(null);
  const [key, setKey] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [wall, setWall] = useState<string[] | null>(null);
  const watch = useIntelStore((s) => s.watch);

  useEffect(() => {
    const hit = watch ? news.find((s) => s.key === watch.key) : undefined;
    if (hit) {
      setGroup(hit.group || null);
      setKey(hit.key);
      setPlaying(true);
      setWall((w) => (w ? [hit.key, ...w.filter((k) => k !== hit.key)].slice(0, WALL_SIZE) : w));
    }
    // `news` is derived from the catalog; the request and the catalog decide.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watch?.seq, q.data]);

  const groupId = group ?? groups[0]?.id ?? "";
  const inGroup = groups.length ? news.filter((s) => s.group === groupId) : news;
  const current = news.find((s) => s.key === key) ?? inGroup[0] ?? news[0];
  const pick = (k: string) => {
    setKey(k);
    setPlaying(true);
    setWall((w) => (w ? (w.includes(k) ? w : [k, ...w].slice(0, WALL_SIZE)) : w));
  };
  const toggleWall = () => {
    setPlaying(true);
    setWall((w) => (w ? null : [current?.key, ...inGroup.map((s) => s.key)].filter((k, i, all): k is string => !!k && all.indexOf(k) === i).slice(0, WALL_SIZE)));
  };
  const onWall = wall ? wall.map((k) => news.find((s) => s.key === k)).filter((s): s is LiveStream => !!s) : [];

  return (
    <StarkPanel
      title={t("stark.live.news")}
      meta={
        current ? (
          <span className="flex items-center gap-1.5">
            <StarkLamp state={playing ? "on" : "off"} />
            {t(playing ? "stark.live.on_air" : "stark.live.ready")}
          </span>
        ) : undefined
      }
      action={
        current ? (
          <IconButton label={t(wall ? "stark.live.one_channel" : "stark.live.news_wall")} onClick={toggleWall} active={!!wall} testId="stark-live-news-wall">
            <Grid2x2 className="h-3 w-3" aria-hidden />
          </IconButton>
        ) : undefined
      }
      className={className}
      bodyClassName="flex flex-col gap-2"
      testId="stark-live-news"
    >
      {!current ? (
        <p className="text-xs text-muted-foreground">{t(q.isError ? "stark.live.error" : "stark.live.loading")}</p>
      ) : (
        <>
          {groups.length > 1 && (
            <div className="flex gap-1" role="group" aria-label={t("stark.live.news_groups")}>
              {groups.map((g) => (
                <Chip key={g.id} active={g.id === groupId} testId={`stark-live-news-group-${g.id}`} onClick={() => setGroup(g.id)}>
                  {t(`stark.live.news_group_${g.id}`)}
                </Chip>
              ))}
            </div>
          )}
          <div className="flex gap-1 overflow-x-auto pb-0.5" role="group" aria-label={t("stark.live.channels")}>
            {inGroup.map((s) => (
              <Chip
                key={s.key}
                active={wall ? wall.includes(s.key) : s.key === current.key}
                testId={`stark-live-channel-${s.key}`}
                onClick={() => pick(s.key)}
              >
                {s.short}
              </Chip>
            ))}
          </div>
          {wall ? (
            <div className="grid min-h-0 flex-1 grid-cols-2 grid-rows-2 gap-1" data-testid="stark-live-news-grid">
              {onWall.map((s) => (
                <div key={s.key} className="relative min-h-0">
                  <YouTubeFrame stream={s} autoplay className="h-full" />
                  <CamLabel stream={s} onExpand={() => { setKey(s.key); setWall(null); }} />
                </div>
              ))}
            </div>
          ) : (
            <YouTubeFrame stream={current} autoplay={playing} className="flex-1" />
          )}
          <p className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
            <span>{t("stark.live.muted_note")}</span>
            <a href={current.page} target="_blank" rel="noopener noreferrer" className="inline-flex shrink-0 items-center gap-1 text-primary hover:underline">
              {t("stark.live.open_youtube")}
              <ExternalLink className="h-2.5 w-2.5" aria-hidden />
            </a>
          </p>
        </>
      )}
    </StarkPanel>
  );
}

/** LIVE CAMERAS: public city cameras by region, as a grid or one at a time. */
export function LiveCameras({ className }: { className?: string }) {
  const t = useT();
  const q = useLiveCatalog();
  const groups = q.data?.groups ?? [];
  const cams = (q.data?.streams ?? []).filter((s) => s.kind === "camera");
  const [group, setGroup] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [single, setSingle] = useState<string | null>(null);
  const [playAll, setPlayAll] = useState(savedPlayAll);
  const watch = useIntelStore((s) => s.watch);

  useEffect(() => {
    const cam = watch ? cams.find((s) => s.key === watch.key) : undefined;
    if (cam) {
      setGroup(cam.group);
      setSingle(cam.key);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watch?.seq, q.data]);

  const groupId = group ?? groups[0]?.id ?? "";
  const inGroup = cams.filter((s) => s.group === groupId);
  const pages = Math.max(1, Math.ceil(inGroup.length / CAMS_PER_PAGE));
  const shown = inGroup.slice((page % pages) * CAMS_PER_PAGE, (page % pages) * CAMS_PER_PAGE + CAMS_PER_PAGE);
  const big = single ? cams.find((s) => s.key === single) : undefined;

  const togglePlayAll = () => {
    setPlayAll((on) => {
      savePlayAll(!on);
      return !on;
    });
  };

  return (
    <StarkPanel
      title={t("stark.live.cameras")}
      meta={inGroup.length ? fill(t(inGroup.length === 1 ? "stark.live.cam_count_one" : "stark.live.cam_count"), { count: inGroup.length }) : undefined}
      action={
        <div className="flex items-center gap-1">
          {!big && (
            <IconButton label={t(playAll ? "stark.live.stop_all" : "stark.live.play_all")} onClick={togglePlayAll} active={playAll} testId="stark-live-play-all">
              {playAll ? <Pause className="h-3 w-3" aria-hidden /> : <Play className="h-3 w-3" aria-hidden />}
            </IconButton>
          )}
          <IconButton label={t("stark.live.grid")} onClick={() => setSingle(null)} active={!big} testId="stark-live-grid">
            <LayoutGrid className="h-3 w-3" aria-hidden />
          </IconButton>
          <IconButton label={t("stark.live.single")} onClick={() => setSingle(big?.key ?? shown[0]?.key ?? null)} active={!!big} testId="stark-live-single">
            <Square className="h-3 w-3" aria-hidden />
          </IconButton>
        </div>
      }
      className={className}
      bodyClassName="flex flex-col gap-2"
      testId="stark-live-cameras"
    >
      {!groups.length ? (
        <p className="text-xs text-muted-foreground">{t(q.isError ? "stark.live.error" : "stark.live.loading")}</p>
      ) : (
        <>
          <div className="flex items-center gap-1 overflow-x-auto pb-0.5" role="group" aria-label={t("stark.live.groups")}>
            {groups.map((g) => (
              <Chip
                key={g.id}
                active={g.id === groupId}
                testId={`stark-live-group-${g.id}`}
                onClick={() => {
                  setGroup(g.id);
                  setPage(0);
                  setSingle(null);
                }}
              >
                {t(`stark.live.group_${g.id.replace("-", "_")}`)}
              </Chip>
            ))}
            {!big && pages > 1 && (
              <button type="button" onClick={() => setPage((p) => p + 1)} className="ml-auto flex shrink-0 items-center gap-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-primary hover:underline" data-testid="stark-live-next">
                {(page % pages) + 1}/{pages}
                <ChevronRight className="h-3 w-3" aria-hidden />
              </button>
            )}
          </div>
          {big ? (
            <div className="relative flex min-h-0 flex-1 flex-col">
              <YouTubeFrame stream={big} autoplay className="flex-1" />
              <CamLabel stream={big} />
            </div>
          ) : (
            <div className={cn("grid min-h-0 flex-1 gap-1", shown.length > 1 && "grid-cols-2", shown.length > 2 && "grid-rows-2")}>
              {shown.map((cam) => (
                <div key={cam.key} className="relative min-h-0">
                  <YouTubeFrame stream={cam} autoplay={playAll} className="h-full" />
                  <CamLabel stream={cam} onExpand={() => setSingle(cam.key)} />
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </StarkPanel>
  );
}

export function CamLabel({ stream, onExpand }: { stream: LiveStream; onExpand?: () => void }) {
  const t = useT();
  return (
    <div className="pointer-events-none absolute left-1 top-1 flex max-w-[calc(100%-0.5rem)] items-center gap-1">
      <span className="flex min-w-0 items-center gap-1 bg-background/80 px-1.5 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-[0.14em] text-foreground">
        <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-[hsl(var(--stark-hot))]" />
        <span className="truncate">{stream.name}</span>
        {stream.place && <span className="hidden truncate font-normal normal-case tracking-normal text-muted-foreground sm:inline">· {stream.place}</span>}
      </span>
      {onExpand && (
        <button type="button" onClick={onExpand} aria-label={fill(t("stark.live.expand"), { stream: stream.name })} className="pointer-events-auto flex h-4 w-4 items-center justify-center bg-background/80 text-primary hover:text-foreground" data-testid={`stark-live-expand-${stream.key}`}>
          <Maximize2 className="h-2.5 w-2.5" aria-hidden />
        </button>
      )}
    </div>
  );
}
