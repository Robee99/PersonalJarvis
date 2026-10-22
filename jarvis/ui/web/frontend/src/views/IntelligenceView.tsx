import { useMemo, useState } from "react";
import { AlertTriangle, ExternalLink, Flame, Mountain, Waves, Wind, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { MotionConfig } from "framer-motion";
import { StarkShell, useNow } from "@/components/stark/StarkShell";
import { StarkPanel } from "@/components/stark/StarkPanel";
import { WorldDotMap, type MapMarker } from "@/components/stark/WorldDotMap";
import { CommandBar } from "@/components/stark/CommandBar";
import { NewsBroadcast, useDeckNews } from "@/components/stark/WorldPanels";
import {
  REGIONS,
  agoMs,
  eventSeverity,
  feedState,
  quakeSeverity,
  regionOf,
  useNaturalEvents,
  useQuakes,
  type FeedState,
  type Region,
  type Severity,
} from "@/lib/publicData";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The HUD's Intelligence screen: what is happening on the planet right now,
 * from free public feeds only — USGS earthquakes (M2.5+, last day), NASA
 * EONET natural events (storms, wildfires, volcanoes, ice, floods) and the
 * world headlines the Command screen's ticker reads.
 *
 * Every mark on the map is one real event with its source link. The region
 * table, the activity matrix and the alert list are counts of those events,
 * nothing more: no people, no predictions, no invented confidence. A feed
 * that did not answer is shown as down, and an answer that is old as stale.
 */

type Layer = "quake" | "storm" | "fire" | "volcano" | "other";

interface Incident {
  id: string;
  layer: Layer;
  title: string;
  place: string;
  lon: number;
  lat: number;
  at: number;
  severity: Severity;
  region: Region | null;
  link: string | null;
  detail: string;
}

const LAYERS: Array<{ key: Layer; icon: LucideIcon }> = [
  { key: "quake", icon: Waves },
  { key: "storm", icon: Wind },
  { key: "fire", icon: Flame },
  { key: "volcano", icon: Mountain },
  { key: "other", icon: AlertTriangle },
];

const LEVELS = ["low", "moderate", "high", "critical"] as const;
type Level = (typeof LEVELS)[number];

/** A region's activity from its events: many small quakes stay "low". */
export function activityLevel(incidents: Array<Pick<Incident, "severity">>): { score: number; level: Level } {
  const score = incidents.reduce((sum, i) => sum + (i.severity === "high" ? 3 : i.severity === "med" ? 1 : 0.1), 0);
  const level: Level = score < 1 ? "low" : score < 3 ? "moderate" : score < 6 ? "high" : "critical";
  return { score, level };
}

function layerOf(categoryId: string): Layer {
  if (categoryId === "severeStorms") return "storm";
  if (categoryId === "wildfires") return "fire";
  if (categoryId === "volcanoes") return "volcano";
  return "other";
}

export function IntelligenceView() {
  const t = useT();
  const quakesQ = useQuakes();
  const eventsQ = useNaturalEvents();
  const newsQ = useDeckNews();
  const [hidden, setHidden] = useState<Set<Layer>>(() => new Set());
  const [selected, setSelected] = useState<string | null>(null);

  const incidents = useMemo<Incident[]>(() => {
    const out: Incident[] = [];
    for (const q of quakesQ.data?.items ?? []) {
      out.push({
        id: `q-${q.id}`,
        layer: "quake",
        title: q.mag != null ? `M${q.mag.toFixed(1)} ${t("stark.intel.quake")}` : t("stark.intel.quake"),
        place: q.place,
        lon: q.lon,
        lat: q.lat,
        at: q.time_ms,
        severity: quakeSeverity(q),
        region: regionOf(q.lon, q.lat),
        link: q.url,
        detail: [q.depth_km != null ? `${Math.round(q.depth_km)} km ${t("stark.intel.deep")}` : "", q.tsunami ? t("stark.intel.tsunami") : ""]
          .filter(Boolean)
          .join(" · "),
      });
    }
    for (const e of eventsQ.data?.items ?? []) {
      out.push({
        id: `e-${e.id}`,
        layer: layerOf(e.category_id),
        title: e.title,
        place: e.category,
        lon: e.lon,
        lat: e.lat,
        at: e.date_ms,
        severity: eventSeverity(e),
        region: regionOf(e.lon, e.lat),
        link: e.link,
        detail: e.magnitude != null ? `${e.magnitude} ${e.unit ?? ""}`.trim() : "",
      });
    }
    return out.sort((a, b) => b.at - a.at);
  }, [quakesQ.data, eventsQ.data, t]);

  const visible = useMemo(() => incidents.filter((i) => !hidden.has(i.layer)), [incidents, hidden]);
  const markers: MapMarker[] = useMemo(
    () =>
      visible.map((i) => ({
        id: i.id,
        lon: i.lon,
        lat: i.lat,
        tone: i.severity === "high" ? "hot" : i.severity === "med" ? "warn" : "calm",
        weight: i.severity === "high" ? 1 : i.severity === "med" ? 0.6 : 0.2,
        label: `${i.title} — ${i.place}`,
      })),
    [visible],
  );
  const byRegion = useMemo(() => {
    const map = new Map<Region, Incident[]>(REGIONS.map((r) => [r, []]));
    for (const i of visible) if (i.region) map.get(i.region)!.push(i);
    return map;
  }, [visible]);
  const overall = useMemo(() => {
    let worst: Level = "low";
    for (const list of byRegion.values()) {
      const { level } = activityLevel(list);
      if (LEVELS.indexOf(level) > LEVELS.indexOf(worst)) worst = level;
    }
    return worst;
  }, [byRegion]);
  const alerts = visible.filter((i) => i.severity !== "low").sort((a, b) => sevRank(b.severity) - sevRank(a.severity) || b.at - a.at);
  const pick = incidents.find((i) => i.id === selected) ?? null;
  const loading = quakesQ.isPending && eventsQ.isPending;

  const feeds: Array<{ name: string; state: FeedState; at: number | null }> = [
    { name: "USGS", state: feedState(quakesQ), at: quakesQ.data?.fetched_at ?? null },
    { name: "NASA EONET", state: feedState(eventsQ), at: eventsQ.data?.fetched_at ?? null },
    ...(newsQ.data?.sources ?? ["BBC World", "Al Jazeera", "NPR World"]).map((name) => ({
      name,
      state: (newsQ.data ? (newsQ.data.failed.includes(name) ? "down" : "live") : newsQ.isError ? "down" : "loading") as FeedState,
      at: newsQ.data?.fetched_at ?? null,
    })),
  ];
  const liveShare = feeds.filter((f) => f.state === "live" || f.state === "stale").length / feeds.length;

  const toggle = (layer: Layer) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(layer)) next.delete(layer);
      else next.add(layer);
      return next;
    });

  return (
    <MotionConfig reducedMotion="user">
      <StarkShell
        screen="intelligence"
        subtitle={t("stark.intel.subtitle")}
        aside={<WorldClocks />}
        badge={<ActivityBadge level={overall} />}
        testId="stark-intel"
      >
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto px-4 pb-3 pt-1 xl:grid-cols-[minmax(0,1fr)_minmax(320px,25rem)] xl:grid-rows-[minmax(0,1.5fr)_minmax(0,1fr)_auto] xl:overflow-hidden xl:px-6">
          {/* The map: every real event, with layers and the region counts. */}
          <StarkPanel
            title={t("stark.intel.map")}
            meta={
              <span className="flex items-center gap-1.5">
                <LiveDot state={feeds[0].state === "live" || feeds[1].state === "live" ? "live" : feeds[0].state} />
                {t(`stark.feed.${feeds[0].state === "live" || feeds[1].state === "live" ? "live" : feeds[0].state}`)}
              </span>
            }
            className="min-h-[24rem] xl:min-h-0"
            bodyClassName="flex min-h-0 flex-col gap-2"
            testId="stark-intel-map"
          >
            <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={t("stark.intel.layers")}>
              {LAYERS.map(({ key, icon: Icon }) => {
                const on = !hidden.has(key);
                const count = incidents.filter((i) => i.layer === key).length;
                return (
                  <button
                    key={key}
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggle(key)}
                    data-testid={`stark-layer-${key}`}
                    className={cn(
                      "flex items-center gap-1.5 border px-2 py-0.5 text-xs font-semibold uppercase tracking-[0.14em] transition-colors",
                      on ? "border-primary/60 bg-primary/10 text-primary" : "border-primary/15 text-muted-foreground hover:text-foreground",
                    )}
                  >
                    <Icon className="h-3 w-3" aria-hidden />
                    {t(`stark.intel.layer_${key}`)}
                    <span className="tabular-nums text-foreground/70">{count}</span>
                  </button>
                );
              })}
            </div>
            <WorldDotMap
              markers={markers}
              selected={selected}
              onSelect={(id) => setSelected((cur) => (cur === id ? null : id))}
              dotAlpha={0.55}
              className="min-h-[16rem] flex-1"
              testId="stark-intel-world"
            >
              <RegionTable byRegion={byRegion} />
              {pick && <IncidentCard incident={pick} onClose={() => setSelected(null)} />}
              {loading && (
                <p role="status" className="absolute inset-x-0 top-1/2 text-center text-sm text-muted-foreground">
                  {t("stark.intel.loading")}
                </p>
              )}
              {!loading && !incidents.length && (
                <p role="status" className="absolute inset-x-0 top-1/2 text-center text-sm text-[hsl(var(--stark-hot))]" data-testid="stark-intel-empty">
                  {t("stark.intel.no_data")}
                </p>
              )}
              <p className="absolute bottom-1 right-2 text-[10px] font-medium text-muted-foreground">
                {t("stark.intel.attribution")}
              </p>
            </WorldDotMap>
          </StarkPanel>

          {/* Alerts: the moderate and severe events, worst first. */}
          <StarkPanel
            title={`${t("stark.intel.alerts")} (${alerts.length})`}
            className="min-h-[16rem] xl:min-h-0"
            bodyClassName="overflow-y-auto"
            testId="stark-intel-alerts"
          >
            {!alerts.length ? (
              <p className="py-4 text-sm text-muted-foreground">{t(loading ? "stark.intel.loading" : "stark.intel.no_alerts")}</p>
            ) : (
              <ul className="space-y-1.5">
                {alerts.slice(0, 30).map((a) => (
                  <li key={a.id}>
                    <button
                      type="button"
                      onClick={() => setSelected(a.id)}
                      className={cn(
                        "flex w-full items-center gap-3 border border-transparent px-1 py-1 text-left hover:border-primary/30 hover:bg-primary/5",
                        selected === a.id && "border-primary/50 bg-primary/10",
                      )}
                      data-testid="stark-alert"
                      data-severity={a.severity}
                    >
                      <SeverityTag severity={a.severity} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-sans text-sm text-foreground">{a.title}</span>
                        <span className="block truncate text-xs text-muted-foreground">{a.place}</span>
                      </span>
                      <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{agoMs(a.at)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </StarkPanel>

          {/* The last day as a timeline, then the newest headlines. */}
          <div className="grid min-h-0 grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
            <Timeline incidents={visible} onPick={setSelected} />
            <NewsBroadcast className="min-h-[14rem] lg:min-h-0" />
          </div>

          {/* Activity by region: where the events cluster, as a matrix. */}
          <ActivityMatrix byRegion={byRegion} />

          {/* Bottom: the sources, the command bar, and how many answered. */}
          <div className="grid grid-cols-1 items-end gap-3 xl:col-span-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,44rem)_minmax(0,1fr)]">
            <SourcesPanel feeds={feeds} />
            <CommandBar />
            <ReliabilityPanel share={liveShare} feeds={feeds} />
          </div>
        </div>
      </StarkShell>
    </MotionConfig>
  );
}

function sevRank(s: Severity): number {
  return s === "high" ? 2 : s === "med" ? 1 : 0;
}

function SeverityTag({ severity }: { severity: Severity }) {
  const t = useT();
  return (
    <span
      className={cn(
        "flex w-16 shrink-0 items-center justify-center gap-1 py-1 text-[11px] font-bold uppercase tracking-[0.12em]",
        severity === "high" && "bg-[hsl(0_80%_50%/0.18)] text-[hsl(0_90%_62%)]",
        severity === "med" && "bg-[hsl(42_100%_50%/0.15)] text-[hsl(42_100%_58%)]",
        severity === "low" && "bg-primary/10 text-primary",
      )}
    >
      <AlertTriangle className="h-3 w-3" aria-hidden />
      {t(`stark.intel.sev_${severity}`)}
    </span>
  );
}

const CLOCKS: Array<{ key: string; tz: string }> = [
  { key: "new_york", tz: "America/New_York" },
  { key: "london", tz: "Europe/London" },
  { key: "tokyo", tz: "Asia/Tokyo" },
  { key: "sydney", tz: "Australia/Sydney" },
];

function WorldClocks() {
  const t = useT();
  const now = useNow(15_000);
  return (
    <div className="stark-clocks hidden items-stretch 2xl:flex" data-testid="stark-world-clocks">
      {CLOCKS.map(({ key, tz }) => (
        <div key={key} className="flex flex-col px-4 py-1">
          <span className="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">{t(`stark.intel.city_${key}`)}</span>
          <span className="whitespace-nowrap text-lg font-semibold tabular-nums text-foreground">
            {now.toLocaleTimeString(undefined, { timeZone: tz, hour: "2-digit", minute: "2-digit" })}
          </span>
        </div>
      ))}
    </div>
  );
}

function ActivityBadge({ level }: { level: Level }) {
  const t = useT();
  const idx = LEVELS.indexOf(level);
  return (
    <div className="flex flex-col items-end gap-1" title={t("stark.intel.activity_hint")} data-testid="stark-activity" data-level={level}>
      <span className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">{t("stark.intel.activity")}</span>
      <div className="flex items-center gap-2">
        <span className="flex gap-0.5" aria-hidden>
          {LEVELS.map((l, i) => (
            <span key={l} className={cn("h-1.5 w-6", i <= idx ? LEVEL_BG[l] : "bg-primary/15")} />
          ))}
        </span>
        <span className={cn("text-sm font-bold uppercase tracking-[0.16em]", LEVEL_TEXT[level])}>{t(`stark.intel.level_${level}`)}</span>
      </div>
    </div>
  );
}

const LEVEL_TEXT: Record<Level, string> = {
  low: "text-[hsl(var(--stark-ok))]",
  moderate: "text-primary",
  high: "text-[hsl(42_100%_58%)]",
  critical: "text-[hsl(0_90%_62%)]",
};
const LEVEL_BG: Record<Level, string> = {
  low: "bg-[hsl(var(--stark-ok))]",
  moderate: "bg-primary",
  high: "bg-[hsl(42_100%_58%)]",
  critical: "bg-[hsl(0_90%_62%)]",
};

function RegionTable({ byRegion }: { byRegion: Map<Region, Incident[]> }) {
  const t = useT();
  return (
    <div className="absolute left-2 top-2 hidden w-56 border border-primary/25 bg-background/75 p-2 backdrop-blur-sm md:block" data-testid="stark-regions">
      <div className="mb-1 flex justify-between text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
        <span>{t("stark.intel.regions")}</span>
        <span className="tabular-nums">{REGIONS.length}</span>
      </div>
      <ul className="space-y-0.5">
        {REGIONS.map((r) => {
          const list = byRegion.get(r) ?? [];
          const { level } = activityLevel(list);
          return (
            <li key={r} className="flex items-center gap-2 text-xs">
              <span className="min-w-0 flex-1 truncate font-semibold uppercase tracking-[0.1em] text-foreground/85">{t(`stark.region.${r}`)}</span>
              <span className="tabular-nums text-muted-foreground">{list.length}</span>
              <span className={cn("h-1.5 w-1.5 rounded-full", LEVEL_BG[level])} aria-hidden />
              <span className={cn("w-[4.5rem] text-right text-[11px] font-bold uppercase", LEVEL_TEXT[level])}>{t(`stark.intel.level_${level}`)}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function IncidentCard({ incident, onClose }: { incident: Incident; onClose: () => void }) {
  const t = useT();
  return (
    <div className="absolute bottom-6 left-2 w-72 border border-primary/40 bg-background/90 p-3 backdrop-blur" role="dialog" aria-label={incident.title} data-testid="stark-incident">
      <div className="flex items-start gap-2">
        <SeverityTag severity={incident.severity} />
        <button type="button" onClick={onClose} aria-label={t("common.close")} className="ml-auto text-muted-foreground hover:text-foreground">
          <X className="h-4 w-4" />
        </button>
      </div>
      <p className="mt-2 font-sans text-sm font-medium text-foreground">{incident.title}</p>
      <p className="text-xs text-muted-foreground">{incident.place}</p>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 text-xs">
        <dt className="text-muted-foreground">{t("stark.intel.when")}</dt>
        <dd className="text-foreground">{new Date(incident.at).toLocaleString()}</dd>
        <dt className="text-muted-foreground">{t("stark.intel.where")}</dt>
        <dd className="tabular-nums text-foreground">
          {incident.lat.toFixed(2)}°, {incident.lon.toFixed(2)}°
        </dd>
        {incident.detail && (
          <>
            <dt className="text-muted-foreground">{t("stark.intel.detail")}</dt>
            <dd className="text-foreground">{incident.detail}</dd>
          </>
        )}
      </dl>
      {incident.link && (
        <a href={incident.link} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline">
          {t("stark.intel.source")}
          <ExternalLink className="h-3 w-3" aria-hidden />
        </a>
      )}
    </div>
  );
}

/** The last 24 hours, one bar per hour, and the newest events under it. */
function Timeline({ incidents, onPick }: { incidents: Incident[]; onPick: (id: string) => void }) {
  const t = useT();
  const now = useNow(60_000).getTime();
  const hours = useMemo(() => {
    const bins = Array.from({ length: 24 }, () => ({ total: 0, worst: "low" as Severity }));
    for (const i of incidents) {
      const h = Math.floor((now - i.at) / 3_600_000);
      if (h < 0 || h >= 24) continue;
      const bin = bins[23 - h];
      bin.total += 1;
      if (sevRank(i.severity) > sevRank(bin.worst)) bin.worst = i.severity;
    }
    return bins;
  }, [incidents, now]);
  const max = Math.max(1, ...hours.map((h) => h.total));
  return (
    <StarkPanel title={t("stark.intel.timeline")} meta={t("stark.intel.last_24h")} className="min-h-[14rem] lg:min-h-0" bodyClassName="flex min-h-0 flex-col gap-2" testId="stark-timeline">
      <div className="flex h-16 shrink-0 items-end gap-[3px]" role="img" aria-label={t("stark.intel.timeline_label")}>
        {hours.map((h, i) => (
          <span
            key={i}
            className={cn(
              "flex-1",
              h.worst === "high" ? "bg-[hsl(0_90%_62%)]" : h.worst === "med" ? "bg-[hsl(42_100%_58%)]" : "bg-primary/70",
            )}
            style={{ height: `${Math.max(4, (h.total / max) * 100)}%`, opacity: h.total ? 1 : 0.2 }}
            title={`${h.total}`}
          />
        ))}
      </div>
      <div className="flex justify-between text-[10px] tabular-nums text-muted-foreground" aria-hidden>
        <span>-24h</span>
        <span>-12h</span>
        <span>{t("stark.intel.now")}</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            <tr>
              <th className="font-semibold">{t("stark.intel.col_time")}</th>
              <th className="font-semibold">{t("stark.intel.col_event")}</th>
              <th className="hidden font-semibold sm:table-cell">{t("stark.intel.col_region")}</th>
            </tr>
          </thead>
          <tbody className="font-sans">
            {incidents.slice(0, 5).map((i) => (
              <tr key={i.id} className="cursor-pointer hover:bg-primary/5" onClick={() => onPick(i.id)}>
                <td className="py-0.5 pr-2 tabular-nums text-muted-foreground">
                  {new Date(i.at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
                </td>
                <td className="max-w-0 truncate py-0.5 pr-2 text-foreground">{i.title}</td>
                <td className="hidden py-0.5 text-muted-foreground sm:table-cell">{i.region ? t(`stark.region.${i.region}`) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </StarkPanel>
  );
}

function ActivityMatrix({ byRegion }: { byRegion: Map<Region, Incident[]> }) {
  const t = useT();
  return (
    <StarkPanel title={t("stark.intel.matrix")} className="min-h-[14rem] xl:min-h-0" bodyClassName="overflow-y-auto" testId="stark-matrix">
      <div className="grid grid-cols-[6.5rem_repeat(4,minmax(0,1fr))] items-center gap-y-1.5 text-[11px]">
        <span />
        {LEVELS.map((l) => (
          <span key={l} className="text-center font-semibold uppercase tracking-[0.1em] text-muted-foreground">
            {t(`stark.intel.level_${l}`)}
          </span>
        ))}
        {REGIONS.map((r) => {
          const { level } = activityLevel(byRegion.get(r) ?? []);
          return [
            <span key={`${r}-name`} className="truncate font-semibold uppercase tracking-[0.08em] text-foreground/85">
              {t(`stark.region.${r}`)}
            </span>,
            ...LEVELS.map((l, i) => (
              <span key={`${r}-${l}`} className={cn("relative mx-0.5 h-3", MATRIX_BG[i])}>
                {l === level && (
                  <span className={cn("absolute left-1/2 top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white/80", LEVEL_BG[l])} />
                )}
              </span>
            )),
          ];
        })}
      </div>
    </StarkPanel>
  );
}

const MATRIX_BG = [
  "bg-[hsl(var(--stark-ok)/0.18)]",
  "bg-primary/20",
  "bg-[hsl(42_100%_50%/0.22)]",
  "bg-[hsl(0_80%_50%/0.28)]",
];

function LiveDot({ state }: { state: FeedState }) {
  return (
    <span
      aria-hidden
      className={cn(
        "h-2 w-2 rounded-full",
        state === "live" && "bg-[hsl(var(--stark-ok))] shadow-[0_0_6px_hsl(var(--stark-ok))]",
        state === "stale" && "bg-[hsl(42_100%_58%)]",
        state === "down" && "bg-[hsl(var(--stark-hot))]",
        state === "loading" && "border border-muted-foreground",
      )}
    />
  );
}

function SourcesPanel({ feeds }: { feeds: Array<{ name: string; state: FeedState; at: number | null }> }) {
  const t = useT();
  return (
    <StarkPanel title={t("stark.intel.sources")} testId="stark-sources">
      <ul className="grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-2">
        {feeds.map((f) => (
          <li key={f.name} className="flex items-center gap-2 text-xs" data-testid="stark-source" data-state={f.state}>
            <LiveDot state={f.state} />
            <span className="min-w-0 flex-1 truncate font-semibold text-foreground/85">{f.name}</span>
            <span
              className={cn(
                "font-bold uppercase tracking-[0.1em]",
                f.state === "live" ? "text-[hsl(var(--stark-ok))]" : f.state === "stale" ? "text-[hsl(42_100%_58%)]" : f.state === "down" ? "text-[hsl(var(--stark-hot))]" : "text-muted-foreground",
              )}
            >
              {t(`stark.feed.${f.state}`)}
            </span>
          </li>
        ))}
      </ul>
    </StarkPanel>
  );
}

function ReliabilityPanel({ share, feeds }: { share: number; feeds: Array<{ state: FeedState }> }) {
  const t = useT();
  const r = 26;
  const c = 2 * Math.PI * r;
  const answered = feeds.filter((f) => f.state === "live" || f.state === "stale").length;
  return (
    <StarkPanel title={t("stark.intel.reliability")} testId="stark-reliability" bodyClassName="flex items-center gap-4">
      <svg width={72} height={72} viewBox="0 0 64 64" aria-hidden>
        <circle cx={32} cy={32} r={r} fill="none" stroke="hsl(var(--primary) / 0.15)" strokeWidth={6} />
        <circle
          cx={32}
          cy={32}
          r={r}
          fill="none"
          stroke="hsl(var(--primary))"
          strokeWidth={6}
          strokeDasharray={`${c * share} ${c}`}
          transform="rotate(-90 32 32)"
          style={{ filter: "drop-shadow(0 0 4px hsl(var(--primary)))" }}
        />
        <text x={32} y={37} textAnchor="middle" className="fill-foreground font-hud font-bold" style={{ fontSize: 15 }}>
          {Math.round(share * 100)}%
        </text>
      </svg>
      <p className="text-sm text-muted-foreground">
        <span className="block text-2xl font-semibold tabular-nums text-foreground">
          {answered} / {feeds.length}
        </span>
        {t("stark.intel.feeds_answering")}
      </p>
    </StarkPanel>
  );
}
