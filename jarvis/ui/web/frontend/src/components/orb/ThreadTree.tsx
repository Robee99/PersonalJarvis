/**
 * The project's conversations as one branching thread: the trunk is the
 * project, each branch something that hangs off it (a conversation as a
 * topic, a mission as an experiment, a decision, a subtask, a blocker), and
 * under a branch what was said or produced inside it. Newest first; long
 * trunks page, busy branches start folded, and branch kinds filter in place,
 * so the shape stays readable as a project grows.
 */
import { useMemo, useState } from "react";
import {
  Ban,
  ChevronDown,
  ChevronRight,
  Diamond,
  FileText,
  Flag,
  FlaskConical,
  ListTodo,
  MessagesSquare,
  type LucideIcon,
} from "lucide-react";

import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { HEALTH_HEX, isoDay, type BranchKind, type ProjectThread, type ThreadBranch } from "./brainMap";
import { branchKindCounts, filterThread } from "./projectView";

export const BRANCH_ICON: Record<BranchKind, LucideIcon> = {
  topic: MessagesSquare,
  decision: Diamond,
  experiment: FlaskConical,
  subtask: ListTodo,
  blocker: Ban,
  milestone: Flag,
  artifact: FileText,
};

const TRUNK_PAGE = 25;
const FOLD_OVER = 4;

function BranchRow({
  branch,
  selectedId,
  onOpen,
}: {
  branch: ThreadBranch;
  selectedId: string | null;
  onOpen(branch: ThreadBranch): void;
}) {
  const t = useT();
  const [open, setOpen] = useState(branch.children.length <= FOLD_OVER);
  const Icon = BRANCH_ICON[branch.branch];
  const colour = HEALTH_HEX[branch.status];
  const kids = [...branch.children].reverse();
  return (
    <li className="relative pl-4">
      {/* The connector from the parent's line into this branch. */}
      <span aria-hidden className="absolute left-0 top-[15px] h-px w-3.5 bg-cyan-400/25" />
      <div
        className={cn(
          "group flex items-center gap-1.5 rounded-lg px-1.5 py-1",
          selectedId === branch.id ? "bg-cyan-400/10 ring-1 ring-cyan-300/40" : "hover:bg-white/5",
        )}
      >
        {branch.children.length > 0 ? (
          <button
            type="button"
            aria-expanded={open}
            aria-label={t(open ? "orb2.thread.fold" : "orb2.thread.unfold")}
            onClick={() => setOpen((v) => !v)}
            className="text-slate-500 hover:text-cyan-200"
          >
            {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
        ) : (
          <span className="w-3.5" />
        )}
        <button
          type="button"
          data-testid="orb2-branch"
          data-branch={branch.branch}
          onClick={() => onOpen(branch)}
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
          title={branch.status_reason}
        >
          <span
            aria-hidden
            className={cn("h-2 w-2 shrink-0 rounded-full", branch.assignment === "suggested" && "bg-transparent ring-1")}
            style={
              branch.assignment === "suggested"
                ? { boxShadow: `inset 0 0 0 1px ${colour}` }
                : { background: colour, boxShadow: `0 0 6px ${colour}` }
            }
          />
          <Icon className="h-3.5 w-3.5 shrink-0 text-cyan-200/70" aria-hidden />
          <span className="truncate text-[12px] text-slate-200">{branch.title || t("orb2.thread.untitled")}</span>
          <span className="ml-auto shrink-0 pl-2 font-mono text-[10px] text-slate-500">
            {isoDay(branch.last_activity_ms || branch.first_seen_ms)}
          </span>
        </button>
        {!open && branch.children.length > 0 && (
          <span className="shrink-0 rounded-full bg-white/5 px-1.5 font-mono text-[10px] text-slate-400">
            {branch.children.length + branch.hidden_children}
          </span>
        )}
      </div>
      {open && kids.length > 0 && (
        <ul className="ml-[13px] border-l border-cyan-400/20">
          {kids.map((child) => (
            <BranchRow key={child.id} branch={child} selectedId={selectedId} onOpen={onOpen} />
          ))}
          {branch.hidden_children > 0 && (
            <li className="pl-4 pt-0.5 font-mono text-[10px] text-slate-500">
              {fill(t("orb2.thread.hidden_children"), { count: branch.hidden_children })}
            </li>
          )}
        </ul>
      )}
    </li>
  );
}

export function ThreadTree({
  thread,
  title,
  selectedId,
  onOpen,
}: {
  thread: ProjectThread;
  title: string;
  selectedId: string | null;
  onOpen(branch: ThreadBranch): void;
}) {
  const t = useT();
  const [kinds, setKinds] = useState<Set<BranchKind>>(new Set());
  const [limit, setLimit] = useState(TRUNK_PAGE);
  const present = branchKindCounts(thread);
  const branches = useMemo(() => [...filterThread(thread.branches, kinds)].reverse(), [thread, kinds]);
  const toggle = (kind: BranchKind) =>
    setKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });

  if (!thread.branches.length) {
    return <p className="p-2 text-xs leading-5 text-slate-400">{t("orb2.thread.empty")}</p>;
  }
  return (
    <div data-testid="orb2-thread">
      <div role="group" aria-label={t("orb2.thread.filter_label")} className="mb-2 flex flex-wrap gap-1">
        {present.map(([kind, count]) => {
          const Icon = BRANCH_ICON[kind];
          return (
            <button
              key={kind}
              type="button"
              aria-pressed={kinds.has(kind)}
              onClick={() => toggle(kind)}
              className={cn(
                "flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px]",
                kinds.has(kind) ? "border-cyan-300/50 bg-cyan-400/10 text-cyan-100" : "border-white/10 text-slate-300 hover:border-white/25",
              )}
            >
              <Icon className="h-3 w-3" aria-hidden />
              {t(`orb2.branch.${kind}`)}
              <span className="font-mono text-slate-500">{count}</span>
            </button>
          );
        })}
      </div>
      <div className="flex items-center gap-2 px-1 pb-1">
        <span aria-hidden className="h-2.5 w-2.5 rounded-full border-2 border-cyan-300 shadow-[0_0_10px_rgba(79,209,232,0.7)]" />
        <span className="font-mono text-[10px] uppercase tracking-[0.25em] text-cyan-200/90">
          {t("orb2.thread.trunk")} · {title}
        </span>
      </div>
      <ul className="ml-[5px] border-l border-cyan-300/40">
        {branches.slice(0, limit).map((branch) => (
          <BranchRow key={branch.id} branch={branch} selectedId={selectedId} onOpen={onOpen} />
        ))}
      </ul>
      {branches.length > limit && (
        <button
          type="button"
          onClick={() => setLimit((n) => n + TRUNK_PAGE)}
          className="mt-1.5 w-full rounded-lg border border-white/10 py-1 text-[11px] text-slate-300 hover:border-cyan-300/40"
        >
          {fill(t("orb2.thread.older"), { count: branches.length - limit })}
        </button>
      )}
      {thread.hidden_branches > 0 && (
        <p className="mt-1.5 px-1 font-mono text-[10px] text-slate-500">
          {fill(t("orb2.thread.hidden_branches"), { count: thread.hidden_branches })}
        </p>
      )}
    </div>
  );
}
