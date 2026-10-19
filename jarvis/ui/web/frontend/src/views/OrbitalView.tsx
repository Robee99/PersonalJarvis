import { useMemo, useRef } from "react";
import { MotionConfig } from "framer-motion";
import { StarkShell, useNow } from "@/components/stark/StarkShell";
import { StarkPanel } from "@/components/stark/StarkPanel";
import { ArcHalo } from "@/components/stark/ArcHalo";
import { OrbitGlobe, type OrbitMarker } from "@/components/stark/OrbitGlobe";
import { WorldDotMap, type MapMarker, type MapPath } from "@/components/stark/WorldDotMap";
import { CommandBar } from "@/components/stark/CommandBar";
import { readCity } from "@/components/stark/WeatherGlobe";
import { useElementSize } from "@/hooks/useElementSize";
import { groundTrack, nextPasses, propagate, type Pass } from "@/lib/orbit";
import { agoMs, feedState, kpLevel, useIss, useSpace, useStations, type FeedState, type Station } from "@/lib/publicData";
import { useEventStore } from "@/store/events";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The HUD's Orbital screen: the space above the owner, from free public data.
 *
 *  - The ISS where it is now (a public tracking service, every five seconds)
 *    and its path for the next orbit, worked out from CelesTrak's published
 *    elements (approximate, and labelled so).
 *  - The crewed stations and their orbits (CelesTrak's stations group).
 *  - Space weather: the planetary Kp index and the 10.7 cm solar flux
 *    (NOAA SWPC).
 *  - The next close approaches of near-Earth objects (NASA/JPL CNEOS).
 *
 * A value a source did not give is "—"; a source that is down says so.
 */
const ISS_ID = 25544;

export function OrbitalView() {
  const t = useT();
  const issQ = useIss();
  const stationsQ = useStations();
  const spaceQ = useSpace();
  const voiceState = useEventStore((s) => s.voiceState);
  const now = useNow(5_000).getTime();
  const city = readCity();

  const iss = issQ.data?.available && issQ.data.lat != null && issQ.data.lon != null ? issQ.data : null;
  const stations = stationsQ.data?.items ?? [];
  const issEl = stations.find((s) => s.norad_id === ISS_ID) ?? null;
  // Distinct objects: the station group lists modules and visiting craft
  // too; the first element of each name family is enough to place them.
  const shown = useMemo(() => stations.slice(0, 24), [stations]);

  const track = useMemo(() => (issEl ? groundTrack(issEl, now, 95, 45) : []), [issEl, Math.floor(now / 60_000)]); // eslint-disable-line react-hooks/exhaustive-deps
  const positions = useMemo(
    () => shown.filter((s) => s.norad_id !== ISS_ID).map((s) => ({ station: s, at: propagate(s, now) })),
    [shown, now],
  );

  const globeMarkers: OrbitMarker[] = [
    ...track.flat().filter((_, i) => i % 2 === 0).map((p) => ({ lat: p.lat, lon: p.lon, size: 0.012, kind: "track" as const })),
    ...positions.map((p) => ({ lat: p.at.lat, lon: p.at.lon, size: 0.03, kind: "station" as const })),
    ...(city ? [{ lat: city.lat, lon: city.lon, size: 0.04, kind: "home" as const }] : []),
    ...(iss ? [{ lat: iss.lat!, lon: iss.lon!, size: 0.07, kind: "iss" as const }] : []),
  ];
  const mapMarkers: MapMarker[] = [
    ...positions.slice(0, 12).map((p) => ({ id: `s-${p.station.norad_id}`, lon: p.at.lon, lat: p.at.lat, tone: "calm" as const, weight: 0.15, label: p.station.name })),
    ...(city ? [{ id: "home", lon: city.lon, lat: city.lat, tone: "warn" as const, weight: 0.2, label: city.name }] : []),
    ...(iss ? [{ id: "iss", lon: iss.lon!, lat: iss.lat!, tone: "hot" as const, weight: 0.9, label: "ISS" }] : []),
  ];
  const mapPaths: MapPath[] = track.map((run, i) => ({ id: `iss-${i}`, points: run, tone: "hot", dashed: true }));

  const stageRef = useRef<HTMLDivElement>(null);
  const stage = useElementSize(stageRef);
  const halo = Math.max(260, Math.min(620, Math.min(stage.width, stage.height) - 8 || 460));

  return (
    <MotionConfig reducedMotion="user">
      <StarkShell
        screen="orbital"
        subtitle={t("stark.orbit.subtitle")}
        badge={<FeedBadge state={feedState(issQ)} />}
        testId="stark-orbital"
      >
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto px-4 pb-3 pt-1 lg:grid-cols-[minmax(260px,22rem)_minmax(0,1fr)_minmax(300px,24rem)] lg:grid-rows-[minmax(0,1fr)_auto] lg:overflow-hidden xl:px-6">
          {/* LEFT: the ISS now, and where it goes next. */}
          <div className="order-2 flex min-h-0 flex-col gap-3 lg:order-none lg:overflow-y-auto [&>*]:shrink-0">
            <IssPanel iss={iss} state={feedState(issQ)} at={issQ.data?.time_ms ?? null} />
            <StarkPanel title={t("stark.orbit.track")} meta={t("stark.orbit.approx")} className="shrink-0" testId="stark-track">
              <WorldDotMap markers={mapMarkers} paths={mapPaths} dotAlpha={0.5} className="aspect-[2/1] w-full" testId="stark-track-map" />
              <p className="mt-1 text-[10px] text-muted-foreground">{t("stark.orbit.track_hint")}</p>
            </StarkPanel>
            <PassesPanel el={issEl} city={city} now={now} />
          </div>

          {/* CENTRE: the globe in its halo. */}
          <div ref={stageRef} className="order-1 flex min-h-[24rem] items-center justify-center lg:order-none lg:min-h-0">
            <ArcHalo size={halo} voiceState={voiceState} busy={false} context={null} title={t("stark.orbit.halo")}>
              <div style={{ width: Math.round(halo * 0.74), height: Math.round(halo * 0.74) }}>
                <OrbitGlobe markers={globeMarkers} facing={iss ? { lat: iss.lat!, lon: iss.lon! } : city} className="h-full w-full" />
              </div>
            </ArcHalo>
          </div>

          {/* RIGHT: stations, space weather, near-Earth objects. */}
          <div className="order-3 flex min-h-0 flex-col gap-3 lg:order-none">
            <StationsPanel stations={shown} state={feedState(stationsQ)} />
            <SpaceWeatherPanel q={spaceQ} />
            <NeoPanel q={spaceQ} />
          </div>

          {/* BOTTOM: the ISS's orbit, the command bar, and where you are. */}
          <div className="order-4 hidden lg:order-none lg:col-start-1 lg:row-start-2 lg:block">
            <OrbitPanel el={issEl} />
          </div>
          <div className="order-4 flex justify-center lg:order-none lg:col-start-2 lg:row-start-2">
            <CommandBar />
          </div>
          <div className="order-5 lg:order-none lg:col-start-3 lg:row-start-2">
            <PositionPanel iss={iss} />
          </div>
        </div>
      </StarkShell>
    </MotionConfig>
  );
}

function FeedBadge({ state }: { state: FeedState }) {
  const t = useT();
  return (
    <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em]" data-testid="stark-orbit-feed" data-state={state}>
      <span className="text-muted-foreground">{t("stark.orbit.tracking")}</span>
      <span className={cn(state === "live" ? "text-[hsl(var(--stark-ok))]" : state === "down" ? "text-[hsl(var(--stark-hot))]" : "text-primary")}>
        {t(`stark.feed.${state}`)}
      </span>
    </div>
  );
}

function Readout({ label, value, testId }: { label: string; value: string; testId?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-primary/10 py-1" data-testid={testId}>
      <dt className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">{label}</dt>
      <dd className="text-sm font-semibold tabular-nums text-foreground">{value}</dd>
    </div>
  );
}

const fmt = (n: number | null | undefined, digits = 1, unit = "") => (n == null ? "—" : `${n.toFixed(digits)}${unit}`);
const latStr = (lat: number) => `${Math.abs(lat).toFixed(2)}° ${lat >= 0 ? "N" : "S"}`;
const lonStr = (lon: number) => `${Math.abs(lon).toFixed(2)}° ${lon >= 0 ? "E" : "W"}`;

function IssPanel({ iss, state, at }: { iss: { lat?: number; lon?: number; alt_km?: number; vel_kmh?: number; visibility?: string } | null; state: FeedState; at: number | null }) {
  const t = useT();
  useNow(5_000);
  return (
    <StarkPanel
      title={t("stark.orbit.iss")}
      meta={
        <span className={cn(state === "live" ? "text-[hsl(var(--stark-ok))]" : state === "down" ? "text-[hsl(var(--stark-hot))]" : undefined)}>
          {t(`stark.feed.${state}`)} {at ? `· ${agoMs(at)}` : ""}
        </span>
      }
      testId="stark-iss"
    >
      {!iss ? (
        <p className="py-3 text-sm text-muted-foreground">{t(state === "down" ? "stark.orbit.iss_down" : "stark.orbit.loading")}</p>
      ) : (
        <dl>
          <Readout label={t("stark.orbit.lat")} value={latStr(iss.lat!)} testId="stark-iss-lat" />
          <Readout label={t("stark.orbit.lon")} value={lonStr(iss.lon!)} />
          <Readout label={t("stark.orbit.alt")} value={fmt(iss.alt_km, 1, " km")} />
          <Readout label={t("stark.orbit.vel")} value={iss.vel_kmh != null ? `${(iss.vel_kmh / 3600).toFixed(2)} km/s` : "—"} />
          <Readout
            label={t("stark.orbit.light")}
            value={iss.visibility ? t(iss.visibility === "daylight" ? "stark.orbit.daylight" : "stark.orbit.eclipsed") : "—"}
          />
        </dl>
      )}
    </StarkPanel>
  );
}

function StationsPanel({ stations, state }: { stations: Station[]; state: FeedState }) {
  const t = useT();
  return (
    <StarkPanel title={t("stark.orbit.stations")} meta={stations.length ? String(stations.length) : undefined} className="min-h-[10rem] flex-1" bodyClassName="overflow-y-auto" testId="stark-stations">
      {!stations.length ? (
        <p className="py-3 text-sm text-muted-foreground">{t(state === "down" ? "stark.orbit.source_down" : "stark.orbit.loading")}</p>
      ) : (
        <table className="w-full text-left text-xs">
          <thead className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            <tr>
              <th className="font-semibold">{t("stark.orbit.name")}</th>
              <th className="whitespace-nowrap text-right font-semibold">{t("stark.orbit.height_km")}</th>
              <th className="text-right font-semibold">{t("stark.orbit.incl_short")}</th>
            </tr>
          </thead>
          <tbody>
            {stations.slice(0, 12).map((s) => (
              <tr key={s.norad_id} className="border-b border-primary/5" data-testid="stark-station">
                <td className="max-w-0 truncate py-0.5 pr-2 font-sans text-foreground" title={s.name}>
                  {s.name}
                </td>
                <td className="whitespace-nowrap py-0.5 text-right tabular-nums text-foreground/85">
                  {Math.round(s.perigee_km)}–{Math.round(s.apogee_km)}
                </td>
                <td className="py-0.5 pl-2 text-right tabular-nums text-muted-foreground">{s.inclination.toFixed(1)}°</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </StarkPanel>
  );
}

function SpaceWeatherPanel({ q }: { q: ReturnType<typeof useSpace> }) {
  const t = useT();
  const d = q.data;
  const kp = d?.kp ?? null;
  const level = kp ? kpLevel(kp.value) : null;
  const series = kp?.series ?? [];
  return (
    <StarkPanel title={t("stark.orbit.weather")} meta={d?.stale ? t("stark.feed.stale") : undefined} testId="stark-space-weather" bodyClassName="flex gap-3">
      <dl className="min-w-0 flex-1">
        <Readout
          label={t("stark.orbit.kp")}
          value={kp ? `${kp.value.toFixed(1)} · ${t(`stark.orbit.kp_${level!.key}`)}${level!.storm ? ` ${level!.storm}` : ""}` : "—"}
          testId="stark-kp"
        />
        <Readout label={t("stark.orbit.flux")} value={d?.f107 ? `${Math.round(d.f107.value)} sfu` : "—"} />
        {!d && <p className="pt-2 text-xs text-muted-foreground">{t(q.isError ? "stark.orbit.source_down" : "stark.orbit.loading")}</p>}
      </dl>
      <div className="flex w-28 shrink-0 flex-col">
        <div className="flex h-14 items-end gap-[2px]" role="img" aria-label={t("stark.orbit.kp_series")}>
          {series.map((r) => (
            <span
              key={r.time_ms}
              className={cn("flex-1", r.kp >= 5 ? "bg-[hsl(var(--stark-hot))]" : r.kp >= 4 ? "bg-[hsl(42_100%_58%)]" : "bg-primary/80")}
              style={{ height: `${Math.max(6, (r.kp / 9) * 100)}%` }}
              title={`Kp ${r.kp}`}
            />
          ))}
        </div>
        <span className="mt-1 text-[10px] text-muted-foreground">{t("stark.orbit.kp_series")}</span>
      </div>
    </StarkPanel>
  );
}

function NeoPanel({ q }: { q: ReturnType<typeof useSpace> }) {
  const t = useT();
  const neos = q.data?.neos ?? [];
  return (
    <StarkPanel title={t("stark.orbit.neos")} meta={t("stark.orbit.next_60d")} className="min-h-[10rem] flex-1" bodyClassName="overflow-y-auto" testId="stark-neos">
      {!neos.length ? (
        <p className="py-3 text-sm text-muted-foreground">
          {t(q.data ? (q.data.failed.includes("neos") ? "stark.orbit.source_down" : "stark.orbit.no_neos") : q.isError ? "stark.orbit.source_down" : "stark.orbit.loading")}
        </p>
      ) : (
        <ul className="space-y-1">
          {neos.slice(0, 6).map((n) => (
            <li key={`${n.des}-${n.time_ms}`} className="flex items-center gap-2 text-xs" data-testid="stark-neo">
              <span className={cn("h-2 w-2 shrink-0 rounded-full", n.dist_ld < 1 ? "bg-[hsl(42_100%_58%)]" : "bg-[hsl(var(--stark-ok))]")} aria-hidden />
              <span className="w-20 truncate font-semibold text-foreground">{n.des}</span>
              <span className="text-muted-foreground">{new Date(n.time_ms).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</span>
              <span className={cn("ml-auto font-semibold tabular-nums", n.dist_ld < 1 ? "text-[hsl(42_100%_58%)]" : "text-foreground")} title={`${Math.round(n.dist_km).toLocaleString()} km`}>
                {n.dist_ld.toFixed(2)} LD
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-1 text-[10px] text-muted-foreground">{t("stark.orbit.ld_hint")}</p>
    </StarkPanel>
  );
}

function OrbitPanel({ el }: { el: Station | null }) {
  const t = useT();
  return (
    <StarkPanel title={t("stark.orbit.orbit")} testId="stark-orbit-elements">
      {!el ? (
        <p className="py-2 text-sm text-muted-foreground">{t("stark.orbit.loading")}</p>
      ) : (
        <dl>
          <Readout label={t("stark.orbit.incl")} value={`${el.inclination.toFixed(2)}°`} />
          <Readout label={t("stark.orbit.period")} value={`${el.period_min.toFixed(1)} min`} />
          <Readout label={t("stark.orbit.height")} value={`${Math.round(el.perigee_km)}–${Math.round(el.apogee_km)} km`} />
        </dl>
      )}
    </StarkPanel>
  );
}

/**
 * The ISS's next passes over the owner's city, worked out in the browser from
 * the published elements (recomputed once a minute).
 */
function PassesPanel({ el, city, now }: { el: Station | null; city: { lat: number; lon: number; name: string } | null; now: number }) {
  const t = useT();
  const minute = Math.floor(now / 60_000);
  const passes: Pass[] = useMemo(
    () => (el && city ? nextPasses(el, city, minute * 60_000, 24).slice(0, 5) : []),
    [el, city?.lat, city?.lon, minute], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const time = (ms: number) => new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const day = (ms: number) => new Date(ms).toLocaleDateString(undefined, { weekday: "short" });
  return (
    <StarkPanel title={t("stark.orbit.passes")} meta={t("stark.orbit.next_24h")} className="min-h-[8rem] flex-1" bodyClassName="overflow-y-auto" testId="stark-passes">
      {!city ? (
        <p className="py-2 text-xs text-muted-foreground">{t("stark.orbit.no_city")}</p>
      ) : !el ? (
        <p className="py-2 text-sm text-muted-foreground">{t("stark.orbit.loading")}</p>
      ) : !passes.length ? (
        <p className="py-2 text-sm text-muted-foreground" data-testid="stark-passes-empty">{t("stark.orbit.no_passes")}</p>
      ) : (
        <>
          <ul className="text-xs">
            {passes.map((p) => (
              <li key={p.start_ms} className="flex items-center gap-3 border-b border-primary/5 py-1" data-testid="stark-pass">
                <span className="w-9 text-muted-foreground">{day(p.start_ms)}</span>
                <span className="tabular-nums text-foreground">{time(p.start_ms)}</span>
                <span className={cn("ml-auto tabular-nums", p.max_elev >= 45 ? "text-[hsl(var(--stark-ok))]" : "text-foreground/80")}>
                  {fill(t("stark.orbit.pass_max"), { deg: Math.round(p.max_elev) })}
                </span>
                <span className="w-14 text-right tabular-nums text-muted-foreground">
                  {fill(t("stark.orbit.pass_min"), { min: Math.max(1, Math.round((p.end_ms - p.start_ms) / 60_000)) })}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-1 text-[10px] text-muted-foreground">{t("stark.orbit.passes_hint")}</p>
        </>
      )}
    </StarkPanel>
  );
}

/** Great-circle distance in km between two points. */
export function distanceKm(a: { lat: number; lon: number }, b: { lat: number; lon: number }): number {
  const r = Math.PI / 180;
  const h =
    Math.sin(((b.lat - a.lat) * r) / 2) ** 2 +
    Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(((b.lon - a.lon) * r) / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.min(1, Math.sqrt(h)));
}

function PositionPanel({ iss }: { iss: { lat?: number; lon?: number } | null }) {
  const t = useT();
  const now = useNow(1_000);
  const city = readCity();
  return (
    <StarkPanel title={t("stark.orbit.position")} testId="stark-position">
      <dl>
        <Readout label={t("stark.orbit.utc")} value={now.toISOString().slice(11, 19)} />
        <Readout label={t("stark.orbit.local")} value={now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })} />
        {city ? (
          <>
            <Readout label={city.name} value={`${latStr(city.lat)} ${lonStr(city.lon)}`} />
            {iss && <Readout label={t("stark.orbit.iss_from_you")} value={`${Math.round(distanceKm(city, { lat: iss.lat!, lon: iss.lon! })).toLocaleString()} km`} testId="stark-iss-distance" />}
          </>
        ) : (
          <p className="pt-1 text-xs text-muted-foreground">{t("stark.orbit.no_city")}</p>
        )}
      </dl>
    </StarkPanel>
  );
}
