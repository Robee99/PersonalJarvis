import { useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  BookOpen,
  BrainCircuit,
  List,
  Maximize2,
  MessageSquareText,
  Orbit,
  Search,
  Upload,
  X,
} from "lucide-react";
import { DeckCard, DeckIconButton } from "@/components/deck/DeckCard";
import { MemoryOrb3D } from "@/components/deck/MemoryOrb3D";
import {
  fetchMemoryEntity,
  fetchMemoryNodes,
  fetchMemoryProject,
  fetchMemoryProjects,
  fetchWikiPage,
  matchMemoryNodes,
  matchProjects,
  previewMarkdown,
  searchWiki,
  type MemoryEntity,
  type MemoryStatus,
} from "@/components/deck/hudMemoryApi";
import type { OrbRef } from "@/lib/memoryOrbLayout";
import { useAgentChatStore } from "@/store/agentChat";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { fill, useT } from "@/i18n";

/** What the panel has open: a project, one memory item, or a wiki note. */
export type MemorySelection =
  | { type: "project"; id: string }
  | { type: "entity"; id: string }
  | { type: "note"; slug: string };

const KEY = ["hud2", "memory"] as const;
const SEARCH_DEBOUNCE_MS = 200;
/**
 * Where the choice of view is remembered. A new key on 2026-10-12: the orb
 * became the HUD's default, and a "list" stored under the old key
 * (`hud2.memory.view`) — or written there by the old WebGL fallback — kept
 * the orb out of sight for exactly the people who asked for it.
 */
export const MEMORY_VIEW_KEY = "hud3.memory.view";

/** How the panel shows memory at rest: the 3D orb or the project list. */
export type MemoryView = "list" | "orb";

/**
 * The view last chosen; the orb by default. Where WebGL is missing the orb's
 * frame says so in words and its legend, focus line and steppers still work
 * (MemoryOrb3D), so the orb stays the default there too.
 */
export function savedMemoryView(): MemoryView {
  try {
    const saved = window.localStorage.getItem(MEMORY_VIEW_KEY);
    if (saved === "list" || saved === "orb") return saved;
  } catch {
    // Storage can be blocked (private window, previews); fall through to the default.
  }
  return "orb";
}

/** Which page of the full memory map to open, and whether with the import form up. */
export interface FullMapRequest {
  mode: "projects" | "memory";
  importOpen?: boolean;
}

/** The panel's selection for a node the orb opens; null when the node names nothing. */
export function selectionFromOrb(ref: OrbRef): MemorySelection | null {
  if (ref.type === "note") return ref.slug ? { type: "note", slug: ref.slug } : null;
  if (!ref.id) return null;
  return ref.type === "project" ? { type: "project", id: ref.id } : { type: "entity", id: ref.id };
}

const STATUS_DOT: Record<MemoryStatus, string> = {
  red: "bg-destructive",
  blue: "bg-accent",
  yellow: "bg-warning",
  green: "bg-success",
};

/**
 * Project memory on the HUD — look something up without leaving the screen.
 *
 * Three readers, all existing routes: the projects (`/api/brain-map/projects`,
 * highest priority first), the memory's items for the search box
 * (`/api/brain-map/graph`, matched by title here), and the vault's keyword
 * search (`/api/wiki/search`). Selecting a row opens it IN the panel —
 * a project's state, next actions, blockers and open work
 * (`/projects/{id}`), an item's status and evidence (`/entities/{id}`), a
 * note's opening lines (`/api/wiki/page/{slug}`) — with a back stack, so
 * following a next action into its item and back costs no navigation.
 *
 * Acting on a selection: "Ask" sends a turn about it into the chat beside it
 * (the HUD's real chat — the `jarvis` agent-chat store), and the open
 * buttons open the full memory map over the HUD (`onOpenMap`: the 2D maps,
 * hand control, the data import) or the wiki, for anything the panel should
 * not grow into.
 *
 * A route that fails says "not reachable", never an empty list that would
 * read as "nothing remembered".
 *
 * At rest the panel shows either the project list or the memory orb
 * (`MemoryOrb3D`, the same memory in 3D, following the conversation); the
 * two buttons in the title strip switch, and the choice is remembered.
 * Typing always searches and shows the results as a list; a node opened on
 * the orb opens here, in the same detail view and back stack, and "Back"
 * returns to the orb.
 */
export function HudMemoryPanel({
  className,
  onOpenMap,
}: {
  className?: string;
  /** Open the full memory map over the HUD; absent where there is none. */
  onOpenMap?: (request: FullMapRequest) => void;
}) {
  const t = useT();
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");
  const [stack, setStack] = useState<MemorySelection[]>([]);
  const selection = stack[stack.length - 1] ?? null;
  const [view, setViewState] = useState<MemoryView>(savedMemoryView);
  const setView = (next: MemoryView) => {
    setViewState(next);
    try {
      window.localStorage.setItem(MEMORY_VIEW_KEY, next);
    } catch {
      // Remembering the view is a convenience; without storage it resets.
    }
  };

  useEffect(() => {
    const id = window.setTimeout(() => setQ(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [query]);

  const projects = useQuery({
    queryKey: [...KEY, "projects"],
    queryFn: ({ signal }) => fetchMemoryProjects(signal),
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
  });
  const nodes = useQuery({
    queryKey: [...KEY, "nodes"],
    queryFn: ({ signal }) => fetchMemoryNodes(signal),
    enabled: q.length > 0,
    staleTime: 60_000,
    retry: false,
  });
  const wiki = useQuery({
    queryKey: [...KEY, "wiki", q],
    queryFn: ({ signal }) => searchWiki(q, signal),
    enabled: q.length >= 2,
    staleTime: 30_000,
    retry: false,
  });

  const projectRows = useMemo(() => matchProjects(projects.data ?? [], q), [projects.data, q]);
  const itemRows = useMemo(() => matchMemoryNodes(nodes.data ?? [], q), [nodes.data, q]);
  const noteRows = q.length >= 2 ? (wiki.data ?? []) : [];

  const open = (next: MemorySelection) => setStack((s) => [...s, next]);
  const back = () => setStack((s) => s.slice(0, -1));

  const firstResult = (): MemorySelection | null => {
    if (projectRows[0]) return { type: "project", id: projectRows[0].id };
    if (itemRows[0]) return { type: "entity", id: itemRows[0].id };
    if (noteRows[0]) return { type: "note", slug: noteRows[0].slug };
    return null;
  };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      const first = firstResult();
      if (first) {
        e.preventDefault();
        setStack([first]);
      }
    } else if (e.key === "Escape" && query) {
      e.preventDefault();
      setQuery("");
    }
  };

  const searching = q.length > 0;
  // The orb stays mounted (hidden) under a detail or a search, so going back
  // finds it where it was instead of building a new WebGL context.
  const orbAtRest = view === "orb" && !selection && !searching;
  const openFromOrb = (ref: OrbRef) => {
    const next = selectionFromOrb(ref);
    if (next) setStack([next]);
  };
  const nothing =
    searching &&
    projectRows.length === 0 &&
    itemRows.length === 0 &&
    noteRows.length === 0 &&
    !nodes.isFetching &&
    !wiki.isFetching &&
    !projects.isFetching;

  return (
    <DeckCard
      icon={BrainCircuit}
      title={t("hud2.memory.title")}
      meta={projects.data ? projects.data.length : undefined}
      live={selection !== null}
      variant="bracket"
      onOpen={onOpenMap ? () => onOpenMap({ mode: "projects" }) : undefined}
      openLabel={t("hud3.memory.open_map")}
      actions={
        <div className="flex items-center gap-1">
          <div role="group" aria-label={t("hud2.memory.view_label")} className="flex items-center gap-1">
            <DeckIconButton
              icon={Orbit}
              label={t("hud2.memory.view_orb")}
              active={view === "orb"}
              onClick={() => setView("orb")}
            />
            <DeckIconButton
              icon={List}
              label={t("hud2.memory.view_list")}
              active={view === "list"}
              onClick={() => setView("list")}
            />
          </div>
          {onOpenMap && (
            <>
              <span aria-hidden className="mx-0.5 h-3 w-px bg-border" />
              <DeckIconButton
                icon={Upload}
                label={t("hud3.memory.import")}
                onClick={() => onOpenMap({ mode: "memory", importOpen: true })}
              />
              <DeckIconButton
                icon={Maximize2}
                label={t("hud3.memory.open_map")}
                onClick={() => onOpenMap({ mode: "projects" })}
              />
            </>
          )}
        </div>
      }
      className={className}
      bodyClassName="flex min-h-0 flex-col gap-2"
    >
      <div data-testid="hud-memory" className="flex min-h-0 flex-1 flex-col gap-2">
        {/* The search line stays on top in every state: typing always searches. */}
        <label className="flex items-center gap-2 border-b border-accent/40 pb-1 focus-within:border-accent">
          <Search className="h-3.5 w-3.5 shrink-0 text-accent" aria-hidden />
          <input
            data-testid="hud-memory-search"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              if (stack.length) setStack([]);
            }}
            onKeyDown={onKey}
            placeholder={t("hud2.memory.search_placeholder")}
            aria-label={t("hud2.memory.search_label")}
            className="min-w-0 flex-1 bg-transparent font-mono text-xs text-foreground outline-none placeholder:text-muted-foreground/70"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery("")}
              aria-label={t("hud2.memory.clear")}
              title={t("hud2.memory.clear")}
              className="text-muted-foreground transition-colors hover:text-accent"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </label>

        {view === "orb" && <MemoryOrb3D className={orbAtRest ? undefined : "hidden"} onOpen={openFromOrb} />}

        <div className={cn("min-h-0 flex-1 overflow-y-auto pr-1", orbAtRest && "hidden")}>
          {selection ? (
            <div data-testid="hud-memory-detail" className="flex flex-col gap-2">
              <button
                type="button"
                onClick={back}
                className="flex w-fit items-center gap-1 font-mono text-micro uppercase tracking-[0.16em] text-muted-foreground transition-colors hover:text-accent"
              >
                <ArrowLeft className="h-3 w-3" />
                {t("hud2.memory.back")}
              </button>
              {selection.type === "project" && (
                <ProjectDetail id={selection.id} onOpen={open} onOpenMap={onOpenMap} />
              )}
              {selection.type === "entity" && <EntityDetail id={selection.id} onOpen={open} />}
              {selection.type === "note" && <NoteDetail slug={selection.slug} onOpen={open} />}
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              <Group title={t("hud2.memory.projects")}>
                {projects.isLoading ? (
                  <Muted>{t("hud2.memory.loading")}</Muted>
                ) : projects.isError ? (
                  <Muted>{t("hud2.memory.unavailable")}</Muted>
                ) : projectRows.length === 0 && !searching ? (
                  <Muted>{t("hud2.memory.no_projects")}</Muted>
                ) : (
                  projectRows.slice(0, searching ? 5 : 30).map((p) => (
                    <Row
                      key={p.id}
                      testId="hud-memory-project"
                      status={p.status}
                      title={p.title}
                      sub={fill(t("hud2.memory.counts"), {
                        tasks: p.counts?.open_tasks ?? 0,
                        blockers: p.counts?.open_blockers ?? 0,
                      })}
                      onClick={() => open({ type: "project", id: p.id })}
                    />
                  ))
                )}
              </Group>

              {searching && itemRows.length > 0 && (
                <Group title={t("hud2.memory.items")}>
                  {itemRows.map((n) => (
                    <Row
                      key={n.id}
                      testId="hud-memory-item"
                      status={n.status}
                      title={n.title}
                      sub={t(`hud2.memory.kind.${n.kind}`)}
                      onClick={() => open({ type: "entity", id: n.id })}
                    />
                  ))}
                </Group>
              )}

              {searching && noteRows.length > 0 && (
                <Group title={t("hud2.memory.notes")}>
                  {noteRows.map((h) => (
                    <Row
                      key={h.slug}
                      testId="hud-memory-note"
                      title={h.title || h.slug}
                      sub={h.snippet}
                      onClick={() => open({ type: "note", slug: h.slug })}
                    />
                  ))}
                </Group>
              )}

              {searching && wiki.isError && <Muted>{t("hud2.memory.unavailable")}</Muted>}
              {nothing && <Muted testId="hud-memory-nohits">{fill(t("hud2.memory.no_hits"), { q })}</Muted>}
            </div>
          )}
        </div>
      </div>
    </DeckCard>
  );
}

function ProjectDetail({
  id,
  onOpen,
  onOpenMap,
}: {
  id: string;
  onOpen: (s: MemorySelection) => void;
  onOpenMap?: (request: FullMapRequest) => void;
}) {
  const t = useT();
  const q = useQuery({
    queryKey: [...KEY, "project", id],
    queryFn: ({ signal }) => fetchMemoryProject(id, signal),
    staleTime: 15_000,
    retry: false,
  });
  if (q.isLoading) return <Muted>{t("hud2.memory.loading")}</Muted>;
  if (q.isError || !q.data) return <Muted>{t("hud2.memory.unavailable")}</Muted>;
  const p = q.data;
  const openTasks = (p.tasks ?? []).filter((x) => x.status !== "green");
  return (
    <>
      <Heading kind={t("hud2.memory.kind.PROJECT")} title={p.title} status={p.status} reason={p.status_reason} />
      {p.summary && <p className="text-xs leading-relaxed text-foreground/85">{p.summary}</p>}
      <Actions
        prompt={fill(t("hud2.memory.ask_project"), { title: p.title })}
        extra={
          onOpenMap && (
            <ActionButton
              icon={Orbit}
              label={t("hud2.memory.open_orb_short")}
              onClick={() => onOpenMap({ mode: "projects" })}
            />
          )
        }
      />
      {(p.next_actions ?? []).length > 0 && (
        <Group title={t("hud2.memory.next_actions")}>
          {p.next_actions.slice(0, 5).map((a) => (
            <Row
              key={`${a.entity_id}-${a.action}`}
              testId="hud-memory-action"
              title={a.action}
              sub={a.why}
              onClick={() => onOpen({ type: "entity", id: a.entity_id })}
            />
          ))}
        </Group>
      )}
      <EntityGroup title={t("hud2.memory.blockers")} items={p.blockers} onOpen={onOpen} />
      <EntityGroup title={t("hud2.memory.tasks")} items={openTasks} onOpen={onOpen} />
      <EntityGroup title={t("hud2.memory.decisions")} items={p.decisions} onOpen={onOpen} />
    </>
  );
}

function EntityDetail({ id, onOpen }: { id: string; onOpen: (s: MemorySelection) => void }) {
  const t = useT();
  const q = useQuery({
    queryKey: [...KEY, "entity", id],
    queryFn: ({ signal }) => fetchMemoryEntity(id, signal),
    staleTime: 15_000,
    retry: false,
  });
  if (q.isLoading) return <Muted>{t("hud2.memory.loading")}</Muted>;
  if (q.isError || !q.data) return <Muted>{t("hud2.memory.unavailable")}</Muted>;
  const e = q.data;
  const kind = t(`hud2.memory.kind.${e.kind}`);
  return (
    <>
      <Heading kind={kind} title={e.title} status={e.status} reason={e.status_reason} />
      {e.summary && <p className="text-xs leading-relaxed text-foreground/85">{e.summary}</p>}
      <Actions prompt={fill(t("hud2.memory.ask_item"), { title: e.title, kind })} />
      {(e.projects ?? []).length > 0 && (
        <Group title={t("hud2.memory.in_projects")}>
          {e.projects.slice(0, 4).map((pid) => (
            <Row key={pid} title={pid} onClick={() => onOpen({ type: "project", id: pid })} testId="hud-memory-project-link" />
          ))}
        </Group>
      )}
      {(e.evidence ?? []).length > 0 && (
        <Group title={t("hud2.memory.evidence")}>
          {e.evidence.slice(0, 3).map((ev, i) => (
            <blockquote
              key={i}
              className="border-l border-accent/40 pl-2 text-xs italic leading-relaxed text-foreground/80"
            >
              “{ev.quote}”
              <span className="mt-0.5 block font-mono text-[10px] not-italic uppercase tracking-[0.12em] text-muted-foreground">
                {ev.source?.source_kind}
              </span>
            </blockquote>
          ))}
        </Group>
      )}
    </>
  );
}

function NoteDetail({ slug, onOpen }: { slug: string; onOpen: (s: MemorySelection) => void }) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const requestWikiPage = useEventStore((s) => s.requestWikiPage);
  const q = useQuery({
    queryKey: [...KEY, "note", slug],
    queryFn: ({ signal }) => fetchWikiPage(slug, signal),
    staleTime: 15_000,
    retry: false,
  });
  if (q.isLoading) return <Muted>{t("hud2.memory.loading")}</Muted>;
  if (q.isError || !q.data) return <Muted>{t("hud2.memory.unavailable")}</Muted>;
  const page = q.data;
  return (
    <>
      <Heading kind={t("hud2.memory.kind.NOTE")} title={page.title || page.slug} reason={page.path} />
      <Actions
        prompt={fill(t("hud2.memory.ask_note"), { title: page.title || page.slug })}
        extra={
          <ActionButton
            testId="hud-memory-open-wiki"
            icon={BookOpen}
            label={t("hud2.memory.open_wiki")}
            onClick={() => {
              requestWikiPage(page.slug);
              setActiveSection("memory");
            }}
          />
        }
      />
      <p data-testid="hud-memory-note-body" className="whitespace-pre-wrap text-xs leading-relaxed text-foreground/85">
        {previewMarkdown(page.body_md)}
      </p>
      {(page.wikilinks ?? []).length > 0 && (
        <Group title={t("hud2.memory.links")}>
          <div className="flex flex-wrap gap-1">
            {page.wikilinks.slice(0, 12).map((link) => (
              <button
                key={link}
                type="button"
                onClick={() => onOpen({ type: "note", slug: link })}
                className="border border-border px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground transition-colors hover:border-accent/60 hover:text-accent"
              >
                {link}
              </button>
            ))}
          </div>
        </Group>
      )}
    </>
  );
}

/** "Ask" plus whatever the selection opens: one row of actions under the title. */
function Actions({ prompt, extra }: { prompt: string; extra?: ReactNode }) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [sending, setSending] = useState(false);
  const ask = async () => {
    if (sending) return;
    setSending(true);
    try {
      // Into the chat beside the panel — the HUD's one conversation.
      const result = await useAgentChatStore.getState().send(prompt);
      const sent = result !== "failed";
      pushToast(sent ? "info" : "error", sent ? t("hud2.memory.ask_sent") : t("hud2.memory.ask_failed"));
    } finally {
      setSending(false);
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <ActionButton
        testId="hud-memory-ask"
        icon={MessageSquareText}
        label={t("hud2.memory.ask")}
        onClick={() => void ask()}
        disabled={sending}
        primary
      />
      {extra}
    </div>
  );
}

function ActionButton({
  icon: Icon,
  label,
  onClick,
  disabled,
  primary,
  testId,
}: {
  icon: typeof Search;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  primary?: boolean;
  testId?: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex items-center gap-1 border px-2 py-1 font-mono text-[10px] uppercase tracking-[0.14em] transition-colors disabled:opacity-50",
        primary
          ? "border-accent/60 text-accent hover:bg-accent/10"
          : "border-border text-muted-foreground hover:border-accent/60 hover:text-accent",
      )}
    >
      <Icon className="h-3 w-3" />
      {label}
    </button>
  );
}

function Heading({
  kind,
  title,
  status,
  reason,
}: {
  kind: string;
  title: string;
  status?: MemoryStatus;
  reason?: string;
}) {
  const t = useT();
  return (
    <div className="flex flex-col gap-0.5">
      <span className="flex items-center gap-1.5 font-mono text-micro uppercase tracking-[0.18em] text-muted-foreground">
        {status && <span aria-hidden className={cn("h-1.5 w-1.5 rounded-full", STATUS_DOT[status])} />}
        {kind}
        {status && <span className="opacity-70">· {t(`hud2.memory.status.${status}`)}</span>}
      </span>
      <h3 data-testid="hud-memory-title" className="text-sm font-semibold leading-snug text-foreground">
        {title}
      </h3>
      {reason && <span className="truncate font-mono text-[10px] text-muted-foreground">{reason}</span>}
    </div>
  );
}

function EntityGroup({
  title,
  items,
  onOpen,
}: {
  title: string;
  items: MemoryEntity[] | undefined;
  onOpen: (s: MemorySelection) => void;
}) {
  if (!items || items.length === 0) return null;
  return (
    <Group title={`${title} · ${items.length}`}>
      {items.slice(0, 5).map((item) => (
        <Row
          key={item.id}
          status={item.status}
          title={item.title}
          sub={item.status_reason}
          onClick={() => onOpen({ type: "entity", id: item.id })}
          testId="hud-memory-entity"
        />
      ))}
    </Group>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1">
      <h4 className="font-mono text-[10px] uppercase tracking-[0.22em] text-accent/80">{title}</h4>
      {children}
    </section>
  );
}

function Row({
  title,
  sub,
  status,
  onClick,
  testId,
}: {
  title: string;
  sub?: string;
  status?: MemoryStatus;
  onClick: () => void;
  testId?: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      className="group/row flex w-full items-start gap-2 border-l border-transparent py-1 pl-1.5 pr-1 text-left transition-colors hover:border-accent/70 hover:bg-accent/5 focus-visible:border-accent focus-visible:bg-accent/5 focus-visible:outline-none"
    >
      <span
        aria-hidden
        className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", status ? STATUS_DOT[status] : "bg-muted-foreground/50")}
      />
      <span className="flex min-w-0 flex-col">
        <span className="truncate text-xs text-foreground group-hover/row:text-accent">{title}</span>
        {sub && <span className="line-clamp-1 text-[11px] text-muted-foreground">{sub}</span>}
      </span>
    </button>
  );
}

function Muted({ children, testId }: { children: ReactNode; testId?: string }) {
  return (
    <p data-testid={testId} className="text-xs text-muted-foreground">
      {children}
    </p>
  );
}

