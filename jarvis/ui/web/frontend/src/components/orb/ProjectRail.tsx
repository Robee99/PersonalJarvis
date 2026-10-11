/**
 * The project list beside the map: search, colour filter, sort, and one row
 * per project with its health ring, the rule's reason, and what it holds.
 * Pages of `PAGE` rows keep a long list fast; arrow keys move through it and
 * Enter opens a project, so the whole rail works without a mouse.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Diamond, ListTodo, MessagesSquare, OctagonAlert, RefreshCw, Search, X } from "lucide-react";

import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { HEALTH_COLOURS, HEALTH_HEX, type HealthRule, type Project, type StatusColour } from "./brainMap";
import { HealthRing } from "./HealthRing";
import { PAGE, SORT_MODES, colourCounts, type ProjectFilter, type SortMode } from "./projectView";

export interface ProjectRailProps {
  all: Project[];
  shown: Project[];
  rules: HealthRule[];
  filter: ProjectFilter;
  onFilter(next: ProjectFilter): void;
  openId: string | null;
  hoverId: string | null;
  onOpen(project: Project): void;
  onHover(project: Project | null): void;
  onReindex(): void;
  reindexing: boolean;
  loading: boolean;
  failed: boolean;
}

export function ProjectRail(props: ProjectRailProps) {
  const t = useT();
  const { all, shown, filter, onFilter, openId, hoverId } = props;
  const [limit, setLimit] = useState(PAGE);
  const [cursor, setCursor] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);
  const counts = useMemo(() => colourCounts(all), [all]);

  // A new search or filter starts at the top of a fresh page.
  useEffect(() => {
    setLimit(PAGE);
    setCursor(0);
  }, [filter]);

  const visible = shown.slice(0, limit);
  const toggleColour = (colour: StatusColour) => {
    const colours = new Set(filter.colours);
    if (colours.has(colour)) colours.delete(colour);
    else colours.add(colour);
    onFilter({ ...filter, colours });
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (!visible.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = Math.min(visible.length - 1, Math.max(0, cursor + (e.key === "ArrowDown" ? 1 : -1)));
      setCursor(next);
      props.onHover(visible[next]);
      listRef.current?.querySelectorAll<HTMLElement>("[data-project-row]")[next]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault();
      props.onOpen(visible[Math.min(cursor, visible.length - 1)]);
    }
  };

  const ruleFor = (colour: StatusColour) =>
    props.rules
      .filter((r) => r.colour === colour)
      .map((r) => `${t(`orb2.health.rule.${r.id}`)}: ${r.when}`)
      .join("\n");

  return (
    <aside
      aria-label={t("orb2.rail.label")}
      data-testid="orb2-rail"
      className="pointer-events-auto flex min-h-0 flex-col rounded-2xl border border-cyan-400/15 bg-black/55 shadow-[0_0_32px_rgba(79,209,232,0.06)] backdrop-blur"
    >
      <div className="border-b border-white/5 p-3">
        <div className="flex items-center justify-between">
          <p className="font-mono text-[10px] uppercase tracking-[0.25em] text-cyan-300/80">
            {fill(t("orb2.rail.count"), { shown: shown.length, total: all.length })}
          </p>
          <button
            type="button"
            onClick={props.onReindex}
            disabled={props.reindexing}
            title={t("orb2.rail.reindex_title")}
            className="flex items-center gap-1 rounded-full px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.15em] text-slate-400 hover:text-cyan-200 disabled:opacity-50"
          >
            <RefreshCw className={cn("h-3 w-3", props.reindexing && "animate-spin")} aria-hidden />
            {t("orb2.rail.reindex")}
          </button>
        </div>
        <label className="mt-2 flex h-8 items-center gap-2 rounded-full border border-cyan-400/20 bg-black/50 px-3 text-slate-300 focus-within:border-cyan-300/60">
          <Search className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <input
            value={filter.query}
            onChange={(e) => onFilter({ ...filter, query: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === "Escape") onFilter({ ...filter, query: "" });
              else onKey(e);
            }}
            placeholder={t("orb2.rail.search_placeholder")}
            aria-label={t("orb2.rail.search_label")}
            className="min-w-0 flex-1 bg-transparent text-xs outline-none placeholder:text-slate-500"
          />
          {filter.query && (
            <button type="button" aria-label={t("orb2.rail.clear")} onClick={() => onFilter({ ...filter, query: "" })}>
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </label>
        <div role="group" aria-label={t("orb2.rail.filter_label")} className="mt-2 flex flex-wrap gap-1.5">
          {HEALTH_COLOURS.map((colour) => (
            <button
              key={colour}
              type="button"
              aria-pressed={filter.colours.has(colour)}
              data-testid={`orb2-filter-${colour}`}
              title={ruleFor(colour)}
              onClick={() => toggleColour(colour)}
              className={cn(
                "flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] transition-colors",
                filter.colours.has(colour) ? "border-white/40 bg-white/10" : "border-white/10 bg-black/40 hover:border-white/25",
                filter.colours.size > 0 && !filter.colours.has(colour) && "opacity-50",
              )}
            >
              <span
                className="h-2 w-2 rounded-full"
                style={{ background: HEALTH_HEX[colour], boxShadow: `0 0 6px ${HEALTH_HEX[colour]}` }}
              />
              <span className="text-slate-200">{t(`orb2.health.colour.${colour}`)}</span>
              <span className="font-mono text-slate-500">{counts[colour]}</span>
            </button>
          ))}
        </div>
        <label className="mt-2 flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.15em] text-slate-500">
          {t("orb2.rail.sort")}
          <select
            value={filter.sort}
            onChange={(e) => onFilter({ ...filter, sort: e.target.value as SortMode })}
            className="rounded border border-white/10 bg-black/60 px-1.5 py-0.5 text-[11px] normal-case tracking-normal text-slate-200"
          >
            {SORT_MODES.map((mode) => (
              <option key={mode} value={mode}>
                {t(`orb2.sort.${mode}`)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <ul ref={listRef} className="min-h-0 flex-1 overflow-y-auto p-1.5" onKeyDown={onKey}>
        {props.loading && <li className="p-3 text-xs text-slate-400">{t("orb2.rail.loading")}</li>}
        {props.failed && <li className="p-3 text-xs text-rose-300">{t("orb2.rail.failed")}</li>}
        {!props.loading && !props.failed && all.length === 0 && (
          <li className="p-3 text-xs leading-5 text-slate-400" data-testid="orb2-empty">
            {t("orb2.rail.empty")}
          </li>
        )}
        {!props.loading && all.length > 0 && shown.length === 0 && (
          <li className="p-3 text-xs text-slate-400">{t("orb2.rail.no_match")}</li>
        )}
        {visible.map((project, i) => {
          const active = project.id === openId;
          const hot = project.id === hoverId || i === cursor;
          return (
            <li key={project.id}>
              <button
                type="button"
                data-project-row
                data-testid="orb2-project-row"
                aria-current={active ? "true" : undefined}
                onClick={() => {
                  setCursor(i);
                  props.onOpen(project);
                }}
                onMouseEnter={() => props.onHover(project)}
                onMouseLeave={() => props.onHover(null)}
                className={cn(
                  "group flex w-full items-start gap-2.5 rounded-xl border px-2 py-2 text-left transition-colors",
                  active
                    ? "border-cyan-300/50 bg-cyan-400/10"
                    : hot
                      ? "border-white/10 bg-white/5"
                      : "border-transparent hover:bg-white/5",
                )}
              >
                <HealthRing health={project.health} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-medium text-slate-100">{project.title}</span>
                  <span className="block truncate text-[11px] text-slate-400" title={project.health.reason}>
                    {project.health.reason}
                  </span>
                  <span className="mt-0.5 flex gap-2.5 font-mono text-[10px] text-slate-500">
                    <span className="flex items-center gap-0.5" title={t("orb2.counts.open_tasks")}>
                      <ListTodo className="h-3 w-3" aria-hidden />
                      {project.health.open_tasks}
                    </span>
                    <span
                      title={t("orb2.counts.open_blockers")}
                      className={cn("flex items-center gap-0.5", project.health.open_blockers > 0 && "text-rose-300/90")}
                    >
                      <OctagonAlert className="h-3 w-3" aria-hidden />
                      {project.health.open_blockers}
                    </span>
                    <span className="flex items-center gap-0.5" title={t("orb2.counts.decisions")}>
                      <Diamond className="h-3 w-3" aria-hidden />
                      {project.counts.decisions}
                    </span>
                    <span className="flex items-center gap-0.5" title={t("orb2.counts.conversations")}>
                      <MessagesSquare className="h-3 w-3" aria-hidden />
                      {project.counts.conversations}
                    </span>
                  </span>
                </span>
              </button>
            </li>
          );
        })}
        {shown.length > limit && (
          <li className="p-1.5">
            <button
              type="button"
              onClick={() => setLimit((n) => n + PAGE)}
              className="w-full rounded-lg border border-white/10 py-1.5 text-[11px] text-slate-300 hover:border-cyan-300/40"
            >
              {fill(t("orb2.rail.more"), { count: shown.length - limit })}
            </button>
          </li>
        )}
      </ul>
    </aside>
  );
}
