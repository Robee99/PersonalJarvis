/**
 * One project, opened: its health and why, progress, what it holds, where it
 * comes from, its conversation thread, and every item one click away.
 *
 * Four tabs keep it glanceable: Overview (health, progress, next actions,
 * sources, files, recent activity), Thread (the branching conversation tree),
 * Items (decisions, tasks, blockers, milestones, conversations, artifacts,
 * with a filter) and Notes (the vault pages the project's items come from,
 * each readable in place). Opening an item shows its branch over the tab,
 * with a breadcrumb back to the trunk.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowLeft, FileText, FolderOpen, GitBranch, X } from "lucide-react";

import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { fetchWikiPage } from "@/components/deck/hudMemoryApi";
import { splitFrontMatter } from "@/components/outputs/MarkdownProse";

import {
  HEALTH_HEX,
  fetchProject,
  isoDay,
  type BranchKind,
  type Entity,
  type HealthRule,
  type Project,
  type ProjectDetail,
  type ThreadBranch,
} from "./brainMap";
import { BranchPanel } from "./BranchPanel";
import { HealthRing } from "./HealthRing";
import { matchesText, pathTo, projectNotes, type ProjectNote } from "./projectView";
import { ThreadTree } from "./ThreadTree";

export type PanelTab = "overview" | "thread" | "items" | "notes";
const TABS: PanelTab[] = ["overview", "thread", "items", "notes"];
const ITEM_LIMIT = 30;

/** An item the thread does not carry (a cut branch) as a branch of its own. */
export function branchOfEntity(entity: Entity): ThreadBranch {
  const kind: Record<string, BranchKind> = {
    CONVERSATION: "topic",
    THREAD: "topic",
    DECISION: "decision",
    TASK: entity.source.source_kind === "mission" || entity.source.source_kind === "quest" ? "experiment" : "subtask",
    BLOCKER: "blocker",
    MILESTONE: "milestone",
    OUTPUT: "artifact",
    DOCUMENT: "artifact",
  };
  return {
    id: entity.id,
    kind: entity.kind,
    branch: kind[entity.kind] ?? "subtask",
    title: entity.title,
    status: entity.status,
    status_reason: entity.status_reason,
    assignment: entity.assignment,
    first_seen_ms: 0,
    last_activity_ms: entity.last_activity_ms,
    source: entity.source,
    children: [],
    hidden_children: 0,
  };
}

function allEntities(detail: ProjectDetail): Entity[] {
  return [
    ...detail.conversations,
    ...detail.decisions,
    ...detail.tasks,
    ...detail.blockers,
    ...detail.milestones,
    ...detail.reports,
  ];
}

/** The breadcrumb path to an item: through the thread when it is there. */
export function resolvePath(detail: ProjectDetail | undefined, id: string | null): ThreadBranch[] {
  if (!detail || !id) return [];
  const found = pathTo(detail.thread.branches, id);
  if (found.length) return found;
  const entity = allEntities(detail).find((e) => e.id === id);
  return entity ? [branchOfEntity(entity)] : [];
}

function Stat({ label, value, warn }: { label: string; value: number; warn?: boolean }) {
  return (
    <div className="rounded-lg border border-white/5 bg-white/[0.03] px-2 py-1.5">
      <p className={cn("font-mono text-base font-semibold", warn ? "text-rose-300" : "text-slate-100")}>{value}</p>
      <p className="truncate font-mono text-[9px] uppercase tracking-[0.15em] text-slate-500">{label}</p>
    </div>
  );
}

function Overview({ detail, onOpenItem }: { detail: ProjectDetail; onOpenItem(id: string): void }) {
  const t = useT();
  const h = detail.health;
  const sources = useMemo(() => {
    const counts = new Map<string, number>();
    for (const e of allEntities(detail)) counts.set(e.source.source_kind, (counts.get(e.source.source_kind) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [detail]);
  return (
    <div className="space-y-3">
      <div>
        <div className="flex items-baseline justify-between font-mono text-[10px] uppercase tracking-[0.2em] text-slate-500">
          <span>{t("orb2.panel.progress")}</span>
          <span data-testid="orb2-progress">
            {h.total ? fill(t("orb2.panel.progress_value"), { done: h.done, total: h.total }) : t("orb2.health.untracked")}
          </span>
        </div>
        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white/5">
          <div
            className="h-full rounded-full"
            style={{
              width: `${Math.round((h.progress ?? 0) * 100)}%`,
              background: HEALTH_HEX[h.colour],
              boxShadow: `0 0 8px ${HEALTH_HEX[h.colour]}`,
            }}
          />
        </div>
      </div>
      <div className="grid grid-cols-4 gap-1.5">
        <Stat label={t("orb2.counts.open_tasks")} value={h.open_tasks} />
        <Stat label={t("orb2.counts.open_blockers")} value={h.open_blockers} warn={h.open_blockers > 0} />
        <Stat label={t("orb2.counts.decisions")} value={detail.counts.decisions} />
        <Stat label={t("orb2.counts.conversations")} value={detail.counts.conversations} />
      </div>
      {detail.next_actions.length > 0 && (
        <section>
          <h4 className="font-mono text-[10px] uppercase tracking-[0.2em] text-amber-300/80">{t("orb2.panel.next")}</h4>
          <ol className="mt-1 space-y-0.5">
            {detail.next_actions.map((a) => (
              <li key={a.entity_id + a.action}>
                <button
                  type="button"
                  onClick={() => onOpenItem(a.entity_id)}
                  title={a.why}
                  className="w-full truncate rounded-lg px-1.5 py-1 text-left text-[12px] text-slate-200 hover:bg-white/5"
                >
                  › {a.action}
                </button>
              </li>
            ))}
          </ol>
        </section>
      )}
      <section>
        <h4 className="font-mono text-[10px] uppercase tracking-[0.2em] text-slate-500">{t("orb2.panel.sources")}</h4>
        <div className="mt-1 flex flex-wrap gap-1">
          {sources.length === 0 && <span className="text-[11px] text-slate-500">{t("orb2.panel.no_sources")}</span>}
          {sources.map(([kind, n]) => (
            <span key={kind} className="rounded-full border border-white/10 px-2 py-0.5 font-mono text-[10px] text-slate-300">
              {kind} · {n}
            </span>
          ))}
        </div>
        {detail.branches.map((b) => (
          <p key={b.name + b.path} className="mt-1 flex items-center gap-1 font-mono text-[10px] text-slate-400" title={b.path}>
            <GitBranch className="h-3 w-3" aria-hidden /> {b.name}
          </p>
        ))}
      </section>
      {detail.files.length > 0 && (
        <section>
          <h4 className="font-mono text-[10px] uppercase tracking-[0.2em] text-slate-500">{t("orb2.panel.files")}</h4>
          <ul className="mt-1 space-y-0.5">
            {detail.files.slice(0, 12).map((f) => (
              <li key={f.kind + f.path}>
                <button
                  type="button"
                  disabled={!f.entity_id}
                  onClick={() => f.entity_id && onOpenItem(f.entity_id)}
                  className="flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left font-mono text-[10px] text-slate-400 enabled:hover:bg-white/5"
                  title={f.path}
                >
                  <FolderOpen className="h-3 w-3 shrink-0" aria-hidden />
                  <span className="truncate">{f.path}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {detail.timeline.length > 0 && (
        <section>
          <h4 className="font-mono text-[10px] uppercase tracking-[0.2em] text-slate-500">{t("orb2.panel.activity")}</h4>
          <ol className="mt-1 space-y-0.5">
            {detail.timeline.slice(0, 8).map((ev, i) => (
              <li key={`${ev.entity_id}-${ev.ts_ms}-${i}`}>
                <button
                  type="button"
                  onClick={() => onOpenItem(ev.entity_id)}
                  className="flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left text-[11px] text-slate-300 hover:bg-white/5"
                >
                  <span
                    className="h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{ background: ev.status ? HEALTH_HEX[ev.status] : "#64748b" }}
                  />
                  <span className="font-mono text-[10px] text-slate-500">{isoDay(ev.ts_ms)}</span>
                  <span className="truncate">{ev.title}</span>
                </button>
              </li>
            ))}
          </ol>
        </section>
      )}
      {detail.summary && <p className="text-[11px] leading-5 text-slate-500">{detail.summary}</p>}
    </div>
  );
}

const ITEM_GROUPS = ["blockers", "tasks", "decisions", "milestones", "conversations", "reports"] as const;

function Items({ detail, onOpenItem }: { detail: ProjectDetail; onOpenItem(id: string): void }) {
  const t = useT();
  const [text, setText] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  return (
    <div className="space-y-3">
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={t("orb2.items.filter")}
        aria-label={t("orb2.items.filter")}
        className="h-8 w-full rounded-full border border-cyan-400/20 bg-black/50 px-3 text-xs text-slate-200 outline-none placeholder:text-slate-500 focus:border-cyan-300/60"
      />
      {ITEM_GROUPS.map((group) => {
        const list = detail[group].filter((e) => matchesText(`${e.title} ${e.status_reason}`, text));
        if (!list.length) return null;
        const open = expanded.has(group);
        return (
          <section key={group}>
            <h4 className="font-mono text-[10px] uppercase tracking-[0.2em] text-slate-500">
              {t(`orb2.items.${group}`)} · {list.length}
            </h4>
            <ul className="mt-1 space-y-0.5">
              {(open ? list : list.slice(0, ITEM_LIMIT)).map((e) => (
                <li key={e.id}>
                  <button
                    type="button"
                    data-testid="orb2-item"
                    onClick={() => onOpenItem(e.id)}
                    title={e.status_reason}
                    className="flex w-full items-center gap-1.5 rounded-lg px-1.5 py-1 text-left hover:bg-white/5"
                  >
                    <span
                      className="h-2 w-2 shrink-0 rounded-full"
                      style={
                        e.assignment === "suggested"
                          ? { boxShadow: `inset 0 0 0 1px ${HEALTH_HEX[e.status]}` }
                          : { background: HEALTH_HEX[e.status] }
                      }
                    />
                    <span className="truncate text-[12px] text-slate-200">{e.title}</span>
                    <span className="ml-auto shrink-0 font-mono text-[10px] text-slate-500">{isoDay(e.last_activity_ms)}</span>
                  </button>
                </li>
              ))}
            </ul>
            {list.length > ITEM_LIMIT && !open && (
              <button
                type="button"
                onClick={() => setExpanded((prev) => new Set(prev).add(group))}
                className="mt-0.5 px-1.5 text-[11px] text-cyan-300/80 hover:text-cyan-200"
              >
                {fill(t("orb2.items.show_all"), { count: list.length })}
              </button>
            )}
          </section>
        );
      })}
    </div>
  );
}

function NoteView({ note, onBack, onOpenItem }: { note: ProjectNote; onBack(): void; onOpenItem(id: string): void }) {
  const t = useT();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["orb2", "note", note.slug],
    queryFn: ({ signal }) => fetchWikiPage(note.slug, signal),
    staleTime: 60_000,
  });
  const body = useMemo(() => (data ? splitFrontMatter(data.body_md).body : ""), [data]);
  return (
    <div className="space-y-2" data-testid="orb2-note">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onBack}
          aria-label={t("orb2.notes.back")}
          className="rounded-full p-1 text-slate-400 hover:bg-white/5 hover:text-slate-100"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
        </button>
        <h3 className="min-w-0 flex-1 truncate text-[13px] font-semibold text-slate-100" title={note.locator}>
          {data?.title || note.title}
        </h3>
        <button
          type="button"
          onClick={() => onOpenItem(note.entity_id)}
          className="shrink-0 rounded-full border border-white/10 px-2 py-0.5 font-mono text-[10px] text-slate-300 hover:border-cyan-300/40"
        >
          {t("orb2.notes.open_item")}
        </button>
      </div>
      <p className="truncate font-mono text-[10px] text-slate-500" title={note.locator}>
        {note.locator}
      </p>
      {isLoading && <p className="text-xs text-slate-400">{t("orb2.rail.loading")}</p>}
      {isError && <p className="text-xs text-rose-300">{t("orb2.notes.failed")}</p>}
      {data && (
        <div className="space-y-2 break-words text-[12px] leading-5 text-slate-200 [&_a]:text-cyan-300 [&_code]:font-mono [&_code]:text-[11px] [&_h1]:text-[14px] [&_h1]:font-semibold [&_h2]:text-[13px] [&_h2]:font-semibold [&_h3]:font-semibold [&_li]:ml-4 [&_ol]:list-decimal [&_pre]:overflow-x-auto [&_pre]:rounded [&_pre]:bg-white/5 [&_pre]:p-2 [&_strong]:text-slate-50 [&_ul]:list-disc">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
        </div>
      )}
    </div>
  );
}

function Notes({ notes, onOpenItem }: { notes: ProjectNote[]; onOpenItem(id: string): void }) {
  const t = useT();
  const [text, setText] = useState("");
  const [open, setOpen] = useState<ProjectNote | null>(null);
  const [all, setAll] = useState(false);
  const list = useMemo(() => notes.filter((n) => matchesText(`${n.title} ${n.locator}`, text)), [notes, text]);
  if (open) return <NoteView note={open} onBack={() => setOpen(null)} onOpenItem={onOpenItem} />;
  return (
    <div className="space-y-2">
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={t("orb2.notes.filter")}
        aria-label={t("orb2.notes.filter")}
        className="h-8 w-full rounded-full border border-cyan-400/20 bg-black/50 px-3 text-xs text-slate-200 outline-none placeholder:text-slate-500 focus:border-cyan-300/60"
      />
      {notes.length === 0 && <p className="text-[11px] text-slate-500">{t("orb2.notes.empty")}</p>}
      {notes.length > 0 && (
        <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-slate-500">
          {fill(t("orb2.notes.count"), { count: list.length })}
        </p>
      )}
      <ul className="space-y-0.5">
        {(all ? list : list.slice(0, ITEM_LIMIT)).map((n) => (
          <li key={n.locator}>
            <button
              type="button"
              data-testid="orb2-note-row"
              onClick={() => setOpen(n)}
              title={n.locator}
              className="flex w-full items-center gap-1.5 rounded-lg px-1.5 py-1 text-left hover:bg-white/5"
            >
              <FileText className="h-3 w-3 shrink-0 text-slate-500" aria-hidden />
              <span className="truncate text-[12px] text-slate-200">{n.title}</span>
              <span className="ml-auto shrink-0 font-mono text-[10px] text-slate-500">{isoDay(n.last_activity_ms)}</span>
            </button>
          </li>
        ))}
      </ul>
      {list.length > ITEM_LIMIT && !all && (
        <button
          type="button"
          onClick={() => setAll(true)}
          className="px-1.5 text-[11px] text-cyan-300/80 hover:text-cyan-200"
        >
          {fill(t("orb2.items.show_all"), { count: list.length })}
        </button>
      )}
    </div>
  );
}

export function ProjectPanel({
  project,
  rules,
  branchId,
  onBranch,
  onClose,
  tab,
  onTab,
}: {
  project: Project;
  rules: HealthRule[];
  branchId: string | null;
  onBranch(id: string | null): void;
  onClose(): void;
  tab: PanelTab;
  onTab(tab: PanelTab): void;
}) {
  const t = useT();
  const { data: detail, isLoading, isError } = useQuery({
    queryKey: ["orb2", "project", project.id],
    queryFn: () => fetchProject(project.id),
    staleTime: 20_000,
  });
  const health = detail?.health ?? project.health;
  const rule = rules.find((r) => r.id === health.rule);
  const path = resolvePath(detail, branchId);
  const notes = useMemo(() => (detail ? projectNotes(allEntities(detail)) : []), [detail]);

  return (
    <aside
      aria-label={fill(t("orb2.panel.label"), { title: project.title })}
      data-testid="orb2-panel"
      className="pointer-events-auto flex min-h-0 flex-col rounded-2xl border border-cyan-400/20 bg-black/65 shadow-[0_0_40px_rgba(79,209,232,0.08)] backdrop-blur"
    >
      <header className="border-b border-white/5 p-3">
        <div className="flex items-start gap-3">
          <HealthRing health={health} size={46} />
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-[15px] font-semibold text-slate-50">{project.title}</h2>
            <p
              className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.2em]"
              style={{ color: HEALTH_HEX[health.colour] }}
              data-testid="orb2-health-rule"
              title={rule?.when}
            >
              {t(`orb2.health.colour.${health.colour}`)} · {t(`orb2.health.rule.${health.rule}`)}
            </p>
            <p className="mt-0.5 text-[12px] leading-5 text-slate-300" data-testid="orb2-health-reason">
              {health.reason}
            </p>
            <p className="mt-0.5 truncate text-[10px] text-slate-500" title={project.status_reason}>
              {t("orb2.panel.status")}: {project.status_reason}
              {project.last_activity_ms ? ` · ${isoDay(project.last_activity_ms)}` : ""}
            </p>
          </div>
          <button type="button" aria-label={t("orb2.panel.close")} onClick={onClose} className="text-slate-400 hover:text-slate-100">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div role="tablist" className="mt-3 flex gap-1">
          {TABS.map((id) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id && !path.length}
              data-testid={`orb2-tab-${id}`}
              onClick={() => {
                onBranch(null);
                onTab(id);
              }}
              className={cn(
                "rounded-full px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.18em]",
                tab === id && !path.length ? "bg-cyan-400/15 text-cyan-100" : "text-slate-400 hover:text-slate-200",
              )}
            >
              {t(`orb2.panel.tab_${id}`)}
              {id === "thread" && detail ? ` · ${detail.thread.branches.length + detail.thread.hidden_branches}` : ""}
              {id === "notes" && detail ? ` · ${notes.length}` : ""}
            </button>
          ))}
        </div>
      </header>
      <div className="relative min-h-0 flex-1 overflow-y-auto p-3">
        {isLoading && <p className="text-xs text-slate-400">{t("orb2.rail.loading")}</p>}
        {isError && <p className="text-xs text-rose-300">{t("orb2.panel.failed")}</p>}
        {detail && (
          <>
            {/* The tab stays mounted under an open branch, so "back" keeps its place. */}
            <div className={cn(path.length > 0 && "hidden")}>
              {tab === "overview" && <Overview detail={detail} onOpenItem={(id) => onBranch(id)} />}
              {tab === "thread" && (
                <ThreadTree thread={detail.thread} title={project.title} selectedId={branchId} onOpen={(b) => onBranch(b.id)} />
              )}
              {tab === "items" && <Items detail={detail} onOpenItem={(id) => onBranch(id)} />}
              {tab === "notes" && <Notes notes={notes} onOpenItem={(id) => onBranch(id)} />}
            </div>
            {path.length > 0 && (
              <BranchPanel
                path={path}
                onTrunk={() => {
                  onBranch(null);
                  onTab("thread");
                }}
                onCrumb={(i) => onBranch(path[i].id)}
                onOpen={(b) => onBranch(b.id)}
              />
            )}
          </>
        )}
      </div>
    </aside>
  );
}
