import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ExternalLink, MapPinOff, RotateCcw, X } from "lucide-react";
import { StarkLamp, StarkPanel } from "@/components/stark/StarkPanel";
import { useNow } from "@/components/stark/StarkShell";
import { agoMs } from "@/lib/publicData";
import {
  lastSeen,
  matches,
  safeHref,
  sortEvents,
  type Category,
  type Evidence,
  type IntelEvent,
  type IntelSnapshot,
  type IntelSort,
} from "@/lib/intel";
import { useIntelStore } from "@/store/intel";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

const HOURS = [6, 24, 168, 720] as const;
const EVIDENCE: Evidence[] = ["measured", "agency", "media"];
const SEVERITY_FLOORS = [null, 2, 3, 4] as const;

/** The colour of a severity, from the source's own scale; grey when it gave none. */
export function severityTone(severity: number | null): string {
  if (severity == null) return "bg-muted-foreground/40";
  if (severity >= 4) return "bg-[hsl(var(--destructive))]";
  if (severity >= 3) return "bg-[hsl(var(--stark-hot))]";
  if (severity >= 2) return "bg-[hsl(var(--warning))]";
  return "bg-primary/60";
}

function EvidenceBadge({ event }: { event: IntelEvent }) {
  const t = useT();
  return (
    <span
      className={cn(
        "shrink-0 border px-1 text-[9px] font-semibold uppercase tracking-[0.12em]",
        event.evidence === "media"
          ? "border-[hsl(var(--warning))]/60 text-[hsl(var(--warning))]"
          : event.evidence === "measured"
            ? "border-[hsl(var(--stark-ok))]/50 text-[hsl(var(--stark-ok))]"
            : "border-primary/40 text-primary",
      )}
      title={t(`stark.incidents.evidence_${event.evidence}_note`)}
      data-testid="incident-evidence"
    >
      {t(`stark.incidents.evidence_${event.evidence}`)}
    </span>
  );
}

function Chip({
  on,
  onClick,
  children,
  testId,
}: {
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
  testId?: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      data-testid={testId}
      className={cn(
        "border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] transition-colors",
        on ? "border-primary/60 bg-primary/10 text-primary" : "border-primary/15 text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

/**
 * The World screen's incident panel: the filters, the feeds' status, the
 * incident list and the selected incident's details. It reads and writes the
 * shared intel store, so a selection here flies the globe there and a click
 * on the globe opens the details here.
 */
export function IncidentPanel({
  snapshot,
  loading,
  failed,
  className,
}: {
  snapshot: IntelSnapshot | undefined;
  loading: boolean;
  failed: boolean;
  className?: string;
}) {
  const t = useT();
  const now = useNow(30_000).getTime();
  const filters = useIntelStore((s) => s.filters);
  const setFilters = useIntelStore((s) => s.setFilters);
  const selectedId = useIntelStore((s) => s.selectedId);
  const selectedVia = useIntelStore((s) => s.selectedVia);
  const select = useIntelStore((s) => s.select);
  const clearSelection = useIntelStore((s) => s.clearSelection);
  const resetView = useIntelStore((s) => s.resetView);
  const [sort, setSort] = useState<IntelSort>("severity");
  const listRef = useRef<HTMLUListElement>(null);

  const events = snapshot?.events ?? [];
  const regions = snapshot?.regions ?? [];
  const byId = useMemo(() => new Map(events.map((e) => [e.id, e])), [events]);
  const shown = useMemo(
    () => sortEvents(events.filter((e) => matches(e, filters, regions, now)), sort),
    [events, filters, regions, now, sort],
  );
  const selected = selectedId ? byId.get(selectedId) ?? null : null;

  // A selection made on the globe or by the assistant scrolls into view here.
  useEffect(() => {
    if (!selectedId || selectedVia === "panel") return;
    const rows = listRef.current?.querySelectorAll<HTMLElement>("[data-incident-id]") ?? [];
    const row = Array.from(rows).find((el) => el.dataset.incidentId === selectedId);
    row?.scrollIntoView?.({ block: "nearest" });
  }, [selectedId, selectedVia]);

  const toggleEvidence = (value: Evidence) =>
    setFilters({
      evidence: filters.evidence.includes(value)
        ? filters.evidence.filter((e) => e !== value)
        : [...filters.evidence, value],
    });

  return (
    <StarkPanel
      title={t("stark.incidents.title")}
      meta={fill(t("stark.incidents.count"), { shown: shown.length, total: events.length })}
      action={
        <button
          type="button"
          onClick={resetView}
          title={t("stark.incidents.reset")}
          aria-label={t("stark.incidents.reset")}
          data-testid="incident-reset"
          className="text-muted-foreground hover:text-primary"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden />
        </button>
      }
      className={cn("min-h-[360px]", className)}
      bodyClassName="flex flex-col gap-2 px-3"
      testId="incident-panel"
    >
      {/* Filters */}
      <div className="flex flex-col gap-1.5" data-testid="incident-filters">
        <div className="flex flex-wrap gap-1" role="group" aria-label={t("stark.incidents.time")}>
          {HOURS.map((h) => (
            <Chip key={h} on={filters.hours === h} onClick={() => setFilters({ hours: h })} testId={`incident-hours-${h}`}>
              {t(`stark.incidents.hours_${h}`)}
            </Chip>
          ))}
          <span aria-hidden className="mx-0.5 w-px bg-primary/15" />
          {([null, "hazard", "unrest"] as (Category | null)[]).map((c) => (
            <Chip key={c ?? "all"} on={filters.category === c} onClick={() => setFilters({ category: c })} testId={`incident-category-${c ?? "all"}`}>
              {t(`stark.incidents.category_${c ?? "all"}`)}
            </Chip>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {EVIDENCE.map((e) => (
            <Chip key={e} on={filters.evidence.includes(e)} onClick={() => toggleEvidence(e)} testId={`incident-evidence-${e}`}>
              {t(`stark.incidents.evidence_${e}`)}
            </Chip>
          ))}
          <select
            aria-label={t("stark.incidents.region")}
            value={filters.region ?? ""}
            onChange={(ev) => setFilters({ region: ev.target.value || null })}
            data-testid="incident-region"
            className="h-6 min-w-0 flex-1 border border-primary/20 bg-background px-1 text-[11px] text-foreground"
          >
            <option value="">{t("stark.incidents.region_all")}</option>
            {regions.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
          <select
            aria-label={t("stark.incidents.severity")}
            value={filters.minSeverity ?? ""}
            onChange={(ev) => setFilters({ minSeverity: ev.target.value === "" ? null : Number(ev.target.value) })}
            data-testid="incident-severity"
            className="h-6 border border-primary/20 bg-background px-1 text-[11px] text-foreground"
          >
            {SEVERITY_FLOORS.map((s) => (
              <option key={s ?? "any"} value={s ?? ""}>
                {s == null ? t("stark.incidents.severity_any") : fill(t("stark.incidents.severity_min"), { level: s })}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Feed status */}
      <ul className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px]" data-testid="incident-sources">
        {(snapshot?.sources ?? []).map((s) => (
          <li
            key={s.id}
            className="flex items-center gap-1 text-muted-foreground"
            data-state={s.status}
            title={s.status === "ok" ? fill(t("stark.incidents.fetched"), { ago: agoMs(s.fetched_at, now) }) : t(`stark.incidents.status_${s.status}`)}
          >
            <StarkLamp state={s.status === "ok" ? "on" : s.status === "never" ? "off" : "warn"} />
            {s.name}
          </li>
        ))}
        {failed && !snapshot && <li className="text-[hsl(var(--stark-hot))]">{t("stark.incidents.backend_down")}</li>}
      </ul>

      {selected && <IncidentDetail event={selected} snapshot={snapshot!} now={now} onClose={clearSelection} />}
      {selectedId && !selected && snapshot && (
        <p className="text-[11px] text-muted-foreground" data-testid="incident-gone">
          {t("stark.incidents.gone")}
        </p>
      )}

      {/* The list */}
      <div className="flex items-center justify-between text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
        <span>{t("stark.incidents.list")}</span>
        <span className="flex gap-1">
          {(["severity", "newest"] as IntelSort[]).map((s) => (
            <Chip key={s} on={sort === s} onClick={() => setSort(s)} testId={`incident-sort-${s}`}>
              {t(`stark.incidents.sort_${s}`)}
            </Chip>
          ))}
        </span>
      </div>
      <ul ref={listRef} className="min-h-[8rem] flex-1 overflow-y-auto text-xs" role="listbox" aria-label={t("stark.incidents.list")} data-testid="incident-list">
        {!shown.length && (
          <li className="py-2 text-muted-foreground">
            {t(loading ? "stark.incidents.loading" : events.length ? "stark.incidents.none_match" : "stark.incidents.none")}
          </li>
        )}
        {shown.map((e) => (
          <li key={e.id} role="option" aria-selected={e.id === selectedId} data-incident-id={e.id}>
            <button
              type="button"
              onClick={() => select(e.id, "panel")}
              data-testid="incident-row"
              className={cn(
                "flex w-full items-center gap-2 border-b border-primary/5 py-1 pl-1 text-left hover:bg-primary/5",
                e.id === selectedId && "bg-primary/10",
              )}
            >
              <span aria-hidden className={cn("h-6 w-1 shrink-0", severityTone(e.severity))} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-foreground">{e.title}</span>
                <span className="flex items-center gap-1 truncate text-[10px] text-muted-foreground">
                  {t(`stark.incidents.kind_${e.kind}`)}
                  {e.severity_label && <span className="truncate">· {e.severity_label}</span>}
                  {e.stale && <span className="text-[hsl(var(--warning))]">· {t("stark.incidents.stale")}</span>}
                </span>
              </span>
              {e.lat == null && <MapPinOff className="h-3 w-3 shrink-0 text-muted-foreground" aria-label={t("stark.incidents.no_position")} />}
              <EvidenceBadge event={e} />
              <span className="w-7 shrink-0 text-right tabular-nums text-muted-foreground">{agoMs(lastSeen(e), now)}</span>
            </button>
          </li>
        ))}
      </ul>
    </StarkPanel>
  );
}

function when(ms: number | null): string {
  if (ms == null) return "—";
  return new Date(ms).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

function IncidentDetail({
  event,
  snapshot,
  now,
  onClose,
}: {
  event: IntelEvent;
  snapshot: IntelSnapshot;
  now: number;
  onClose: () => void;
}) {
  const t = useT();
  const select = useIntelStore((s) => s.select);
  const source = snapshot.sources.find((s) => s.id === event.source);
  const related = event.related
    .map((id) => snapshot.events.find((e) => e.id === id))
    .filter((e): e is IntelEvent => !!e);
  const area = event.attrs.position === "area centroid";
  return (
    <article
      className="max-h-[55%] shrink-0 overflow-y-auto border border-primary/25 bg-primary/[0.04] p-2.5 text-xs"
      aria-label={t("stark.incidents.details")}
      data-testid="incident-detail"
      data-incident-id={event.id}
    >
      <header className="flex items-start gap-2">
        <span aria-hidden className={cn("mt-0.5 h-8 w-1 shrink-0", severityTone(event.severity))} />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold leading-snug text-foreground">{event.title}</h3>
          <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
            {t(`stark.incidents.kind_${event.kind}`)} · {source?.name ?? event.source}
          </p>
        </div>
        <button type="button" onClick={onClose} aria-label={t("stark.incidents.close")} className="text-muted-foreground hover:text-foreground">
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      </header>

      <div className="mt-2 flex items-start gap-2">
        <EvidenceBadge event={event} />
        <p className="text-[11px] leading-snug text-muted-foreground">
          {t(`stark.incidents.evidence_${event.evidence}_note`)}
          {event.automated && event.evidence !== "media" && ` ${t("stark.incidents.automated_note")}`}
        </p>
      </div>
      {event.evidence === "media" && (
        <p className="mt-2 flex gap-1.5 border border-[hsl(var(--warning))]/50 p-1.5 text-[11px] text-[hsl(var(--warning))]" data-testid="incident-unconfirmed">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          {t("stark.incidents.unconfirmed")}
        </p>
      )}
      {event.stale && (
        <p className="mt-2 text-[11px] text-[hsl(var(--warning))]" data-testid="incident-stale">
          {fill(t("stark.incidents.stale_note"), { ago: agoMs(source?.fetched_at ?? null, now) })}
        </p>
      )}

      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px]">
        <dt className="text-muted-foreground">{t("stark.incidents.severity")}</dt>
        <dd className="text-foreground">
          {event.severity != null ? `${event.severity}/4 · ${event.severity_label ?? ""}` : event.severity_label ?? t("stark.incidents.not_rated")}
        </dd>
        <dt className="text-muted-foreground">{t("stark.incidents.where")}</dt>
        <dd className="text-foreground">
          {[event.place, event.country].filter(Boolean).join(" · ") || "—"}
          <span className="block text-[10px] text-muted-foreground">
            {event.lat != null && event.lon != null
              ? `${event.lat.toFixed(2)}, ${event.lon.toFixed(2)}${area ? ` · ${t("stark.incidents.area_centroid")}` : ""}`
              : t("stark.incidents.no_position")}
          </span>
        </dd>
        <dt className="text-muted-foreground">{t("stark.incidents.occurred")}</dt>
        <dd className="tabular-nums text-foreground">{when(event.occurred_at)}</dd>
        <dt className="text-muted-foreground">{t("stark.incidents.updated")}</dt>
        <dd className="tabular-nums text-foreground">{when(event.updated_at)}</dd>
        <dt className="text-muted-foreground">{t("stark.incidents.first_seen")}</dt>
        <dd className="tabular-nums text-foreground">{when(event.ingested_at)}</dd>
      </dl>

      {event.summary && (
        <blockquote className="mt-2 border-l-2 border-primary/30 pl-2 text-[11px] text-foreground/85" data-testid="incident-summary">
          {event.summary}
        </blockquote>
      )}

      <h4 className="mt-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">{t("stark.incidents.sources")}</h4>
      <ul className="mt-0.5 space-y-0.5" data-testid="incident-links">
        {event.links.map((l) => {
          const href = safeHref(l.url);
          return (
            <li key={l.url} className="flex items-center gap-1">
              {href ? (
                <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex min-w-0 items-center gap-1 text-primary hover:underline">
                  <span className="truncate">{l.name}</span>
                  <ExternalLink className="h-3 w-3 shrink-0" aria-hidden />
                </a>
              ) : (
                <span className="truncate text-muted-foreground">{l.name}</span>
              )}
            </li>
          );
        })}
        {!event.links.length && <li className="text-muted-foreground">{t("stark.incidents.no_links")}</li>}
      </ul>

      {related.length > 0 && (
        <>
          <h4 className="mt-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">{t("stark.incidents.related")}</h4>
          <ul className="mt-0.5 space-y-0.5" data-testid="incident-related">
            {related.map((r) => (
              <li key={r.id}>
                <button type="button" onClick={() => select(r.id, "panel")} className="flex w-full items-center gap-1.5 text-left hover:text-primary">
                  <EvidenceBadge event={r} />
                  <span className="truncate">{r.title}</span>
                  <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
                    {snapshot.sources.find((s) => s.id === r.source)?.name ?? r.source}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      {source && (
        <p className="mt-2 text-[10px] leading-snug text-muted-foreground">
          {source.attribution}{" "}
          <a href={safeHref(source.terms_url) ?? undefined} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">
            {t("stark.incidents.terms")}
          </a>
        </p>
      )}
    </article>
  );
}
