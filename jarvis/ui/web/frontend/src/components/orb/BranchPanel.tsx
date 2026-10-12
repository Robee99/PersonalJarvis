/**
 * One opened branch of a project thread: its context, its messages, what
 * branches off it and the artifacts it produced, under a breadcrumb that
 * leads back up to the trunk. The trunk view stays mounted underneath, so
 * going back returns to the same place in it.
 *
 * Messages are the conversation's own (agent-chat sessions, newest
 * `MESSAGE_TAIL`); for any other source the evidence quotes are shown, the
 * person's words verbatim with where they were said.
 */
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ChevronRight, FileText } from "lucide-react";

import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

import {
  HEALTH_HEX,
  fetchBranchMessages,
  fetchEntity,
  isoDay,
  type ThreadBranch,
} from "./brainMap";
import { BRANCH_ICON } from "./ThreadTree";

const CLIP = 700;

export function BranchPanel({
  path,
  onTrunk,
  onCrumb,
  onOpen,
}: {
  /** Trunk → … → the opened branch. */
  path: ThreadBranch[];
  onTrunk(): void;
  onCrumb(index: number): void;
  onOpen(branch: ThreadBranch): void;
}) {
  const t = useT();
  const branch = path[path.length - 1];
  const entity = useQuery({
    queryKey: ["orb2", "entity", branch.id],
    queryFn: () => fetchEntity(branch.id),
    staleTime: 30_000,
  });
  const messages = useQuery({
    queryKey: ["orb2", "messages", branch.id],
    queryFn: () => fetchBranchMessages(branch.source),
    enabled: branch.branch === "topic",
    staleTime: 30_000,
  });
  const Icon = BRANCH_ICON[branch.branch];
  const colour = HEALTH_HEX[branch.status];
  const artifacts = branch.children.filter((c) => c.branch === "artifact");
  const subBranches = branch.children.filter((c) => c.branch !== "artifact");
  const evidence = entity.data?.evidence ?? [];

  return (
    <div data-testid="orb2-branch-panel" className="flex min-h-0 flex-col">
      <nav aria-label={t("orb2.branch_panel.breadcrumb")} className="flex flex-wrap items-center gap-1 text-[11px]">
        <button
          type="button"
          data-testid="orb2-back-to-trunk"
          onClick={onTrunk}
          className="flex items-center gap-1 rounded-full border border-cyan-400/25 px-2 py-0.5 font-mono uppercase tracking-[0.15em] text-cyan-200 hover:border-cyan-300/60"
        >
          <ArrowLeft className="h-3 w-3" aria-hidden />
          {t("orb2.thread.trunk")}
        </button>
        {path.map((step, i) => (
          <span key={step.id} className="flex min-w-0 items-center gap-1">
            <ChevronRight className="h-3 w-3 shrink-0 text-slate-600" aria-hidden />
            {i < path.length - 1 ? (
              <button type="button" onClick={() => onCrumb(i)} className="max-w-[9rem] truncate text-slate-400 hover:text-cyan-200">
                {step.title}
              </button>
            ) : (
              <span aria-current="page" className="max-w-[12rem] truncate text-slate-200">
                {step.title}
              </span>
            )}
          </span>
        ))}
      </nav>

      <header className="mt-3 rounded-xl border border-white/10 bg-white/[0.03] p-3">
        <p className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.2em] text-cyan-300/80">
          <Icon className="h-3.5 w-3.5" aria-hidden />
          {t(`orb2.branch.${branch.branch}`)}
          {branch.assignment === "suggested" && <span className="text-amber-300/90">· {t("orb2.item.suggested")}</span>}
        </p>
        <h3 className="mt-1 text-sm font-semibold leading-5 text-slate-100">{branch.title || t("orb2.thread.untitled")}</h3>
        <p className="mt-1 flex items-center gap-1.5 text-[11px] text-slate-400">
          <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: colour, boxShadow: `0 0 6px ${colour}` }} />
          {branch.status_reason || t(`orb2.health.colour.${branch.status}`)}
        </p>
        <p className="mt-1 font-mono text-[10px] text-slate-500">
          {fill(t("orb2.branch_panel.dates"), {
            first: isoDay(branch.first_seen_ms) || "—",
            last: isoDay(branch.last_activity_ms) || "—",
          })}
        </p>
        {entity.data?.summary && <p className="mt-2 text-[12px] leading-5 text-slate-300">{entity.data.summary}</p>}
      </header>

      {subBranches.length > 0 && (
        <section className="mt-3">
          <h4 className="font-mono text-[10px] uppercase tracking-[0.2em] text-slate-500">
            {t("orb2.branch_panel.branches")} · {subBranches.length + branch.hidden_children}
          </h4>
          <ul className="mt-1 space-y-0.5">
            {subBranches.map((child) => {
              const ChildIcon = BRANCH_ICON[child.branch];
              return (
                <li key={child.id}>
                  <button
                    type="button"
                    data-testid="orb2-sub-branch"
                    onClick={() => onOpen(child)}
                    className="flex w-full items-center gap-1.5 rounded-lg px-1.5 py-1 text-left hover:bg-white/5"
                  >
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: HEALTH_HEX[child.status] }} />
                    <ChildIcon className="h-3.5 w-3.5 shrink-0 text-cyan-200/70" aria-hidden />
                    <span className="truncate text-[12px] text-slate-200">{child.title}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {artifacts.length > 0 && (
        <section className="mt-3">
          <h4 className="font-mono text-[10px] uppercase tracking-[0.2em] text-slate-500">{t("orb2.branch_panel.artifacts")}</h4>
          <ul className="mt-1 space-y-0.5">
            {artifacts.map((a) => (
              <li key={a.id}>
                <button
                  type="button"
                  onClick={() => onOpen(a)}
                  className="flex w-full items-center gap-1.5 rounded-lg px-1.5 py-1 text-left hover:bg-white/5"
                  title={a.source.locator}
                >
                  <FileText className="h-3.5 w-3.5 shrink-0 text-amber-200/80" aria-hidden />
                  <span className="truncate text-[12px] text-slate-200">{a.title}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {branch.source.locator && (
        <p className="mt-2 break-all font-mono text-[10px] text-slate-500" title={t("orb2.branch_panel.location")}>
          {branch.source.locator}
        </p>
      )}

      <section className="mt-3" data-testid="orb2-branch-messages">
        <h4 className="font-mono text-[10px] uppercase tracking-[0.2em] text-slate-500">
          {t(messages.data ? "orb2.branch_panel.messages" : "orb2.branch_panel.evidence")}
        </h4>
        {(entity.isLoading || messages.isLoading) && <p className="mt-1 text-[11px] text-slate-500">{t("orb2.rail.loading")}</p>}
        {entity.isError && <p className="mt-1 text-[11px] text-rose-300">{t("orb2.branch_panel.failed")}</p>}
        {messages.data ? (
          messages.data.length ? (
            <ol className="mt-1 space-y-1.5">
              {messages.data.map((m) => (
                <li
                  key={m.seq}
                  className={cn(
                    "rounded-lg border px-2.5 py-1.5 text-[12px] leading-5",
                    m.role === "user" ? "border-cyan-400/20 bg-cyan-400/5 text-slate-100" : "border-white/10 bg-white/[0.03] text-slate-300",
                  )}
                >
                  <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-slate-500">
                    {t(`orb2.branch_panel.role_${m.role}`)} · {isoDay(m.ts_ms)}
                  </p>
                  <p className="whitespace-pre-wrap break-words">
                    {m.text.length > CLIP ? `${m.text.slice(0, CLIP)}…` : m.text}
                  </p>
                </li>
              ))}
            </ol>
          ) : (
            <p className="mt-1 text-[11px] text-slate-500">{t("orb2.branch_panel.no_messages")}</p>
          )
        ) : evidence.length ? (
          <ol className="mt-1 space-y-1.5">
            {evidence.map((ev, i) => (
              <li key={`${ev.source.message_ref}-${i}`} className="rounded-lg border-l-2 border-amber-300/50 bg-white/[0.03] px-2.5 py-1.5">
                <blockquote className="text-[12px] leading-5 text-slate-200">“{ev.quote}”</blockquote>
                <p className="mt-0.5 font-mono text-[10px] text-slate-500">
                  {ev.source.source_kind} · {ev.source.message_ref || ev.source.source_id} · {isoDay(ev.source.ts_ms)}
                </p>
              </li>
            ))}
          </ol>
        ) : (
          !entity.isLoading && <p className="mt-1 text-[11px] text-slate-500">{t("orb2.branch_panel.no_evidence")}</p>
        )}
      </section>

      {(entity.data?.status_history.length ?? 0) > 1 && (
        <section className="mt-3">
          <h4 className="font-mono text-[10px] uppercase tracking-[0.2em] text-slate-500">{t("orb2.branch_panel.history")}</h4>
          <ol className="mt-1 space-y-0.5">
            {entity.data?.status_history.map((h, i) => (
              <li key={`${h.ts_ms}-${i}`} className="flex items-center gap-1.5 text-[11px] text-slate-400">
                <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: HEALTH_HEX[h.status] }} />
                <span className="font-mono text-[10px] text-slate-500">{isoDay(h.ts_ms)}</span>
                <span className="truncate">{h.reason}</span>
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}
