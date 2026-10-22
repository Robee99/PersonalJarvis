import { useMemo, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Plus, X } from "lucide-react";
import { StarkPanel } from "@/components/stark/StarkPanel";
import { useEventStore } from "@/store/events";
import type { TaskSummary } from "@/lib/tasksApi";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * CURRENT TASKS: what is being worked on, and the owner's own to-dos.
 *
 *  - The turn in flight: its steps, with a ring that fills as they finish
 *    (the event store's live steps).
 *  - Scheduled work that is running or due (`/api/tasks`, the backend
 *    scheduler that also runs every agent routine).
 *  - The to-do list (`/api/deck/todos`, one small file in the user's data
 *    folder): add one with +, tick it done, or dismiss it with ×.
 *
 * A list that is empty says so; a source that failed says that instead.
 */
export interface Todo {
  id: string;
  title: string;
  done: boolean;
  created_ms: number;
  done_ms: number | null;
}

const TODOS_KEY = ["deck", "todos"] as const;

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = "";
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // A non-JSON error body: the status says enough.
    }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

async function fetchTodos(): Promise<Todo[]> {
  return (await json<{ todos: Todo[] }>(await fetch("/api/deck/todos", { cache: "no-store" }))).todos;
}

async function addTodo(title: string): Promise<Todo> {
  const res = await fetch("/api/deck/todos", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  return (await json<{ todo: Todo }>(res)).todo;
}

async function patchTodo(id: string, done: boolean): Promise<Todo> {
  const res = await fetch(`/api/deck/todos/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ done }),
  });
  return (await json<{ todo: Todo }>(res)).todo;
}

async function removeTodo(id: string): Promise<void> {
  await json<{ ok: boolean }>(await fetch(`/api/deck/todos/${encodeURIComponent(id)}`, { method: "DELETE" }));
}

async function fetchActiveTasks(): Promise<TaskSummary[]> {
  const res = await fetch("/api/tasks?state=running,scheduled,pending&limit=6", { cache: "no-store" });
  return (await json<{ tasks: TaskSummary[] }>(res)).tasks;
}

export function TasksPanel({ className }: { className?: string }) {
  const t = useT();
  const client = useQueryClient();
  const pushToast = useEventStore((s) => s.pushToast);
  const steps = useEventStore((s) => s.thinkingSteps);
  const todosQ = useQuery({ queryKey: TODOS_KEY, queryFn: fetchTodos, refetchInterval: 30_000 });
  const tasksQ = useQuery({ queryKey: ["deck", "active-tasks"], queryFn: fetchActiveTasks, refetchInterval: 15_000, retry: false });
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const onError = (error: unknown) => pushToast("error", (error as Error).message);
  const refresh = () => client.invalidateQueries({ queryKey: TODOS_KEY });
  const add = useMutation({ mutationFn: addTodo, onSuccess: refresh, onError });
  const toggle = useMutation({
    mutationFn: ({ id, done }: { id: string; done: boolean }) => patchTodo(id, done),
    onMutate: ({ id, done }) => {
      client.setQueryData<Todo[]>(TODOS_KEY, (list) => list?.map((x) => (x.id === id ? { ...x, done } : x)));
    },
    onSettled: refresh,
    onError,
  });
  const dismiss = useMutation({
    mutationFn: removeTodo,
    onMutate: (id) => {
      client.setQueryData<Todo[]>(TODOS_KEY, (list) => list?.filter((x) => x.id !== id));
    },
    onSettled: refresh,
    onError,
  });

  const live = useMemo(() => {
    if (!steps.length) return null;
    const done = steps.filter((s) => s.status !== "active").length;
    const current = [...steps].reverse().find((s) => s.status === "active") ?? steps[steps.length - 1];
    return { done, total: steps.length, current };
  }, [steps]);
  const tasks = tasksQ.data ?? [];
  const todos = todosQ.data ?? [];
  const open = todos.filter((x) => !x.done).length + tasks.length + (live ? 1 : 0);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const title = draft.trim();
    if (!title) return;
    add.mutate(title, {
      onSuccess: () => {
        setDraft("");
        inputRef.current?.focus();
      },
    });
  };

  return (
    <StarkPanel
      title={t("stark.tasks.title")}
      meta={
        <span className="flex h-6 min-w-6 items-center justify-center rounded-full border border-primary/50 px-1.5 text-xs text-foreground" data-testid="stark-tasks-count">
          {open}
        </span>
      }
      action={
        <button
          type="button"
          onClick={() => {
            setAdding((v) => !v);
            window.setTimeout(() => inputRef.current?.focus(), 0);
          }}
          aria-label={t("stark.tasks.add")}
          title={t("stark.tasks.add")}
          aria-expanded={adding}
          data-testid="stark-tasks-add"
          className="ml-1 flex h-6 w-6 items-center justify-center rounded-full text-muted-foreground hover:bg-primary/10 hover:text-primary"
        >
          <Plus className="h-4 w-4" aria-hidden />
        </button>
      }
      className={className}
      testId="stark-tasks"
      bodyClassName="flex min-h-0 flex-col overflow-y-auto"
    >
      {adding && (
        <form onSubmit={submit} className="mb-2 flex gap-1.5" data-testid="stark-tasks-form">
          <label htmlFor="stark-new-todo" className="sr-only">
            {t("stark.tasks.add")}
          </label>
          <input
            id="stark-new-todo"
            ref={inputRef}
            value={draft}
            maxLength={200}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setAdding(false);
            }}
            placeholder={t("stark.tasks.placeholder")}
            className="min-w-0 flex-1 border border-primary/30 bg-transparent px-2 py-1.5 font-sans text-sm text-foreground outline-none focus:border-primary"
          />
          <button
            type="submit"
            disabled={!draft.trim() || add.isPending}
            className="border border-primary/50 px-3 text-xs font-semibold uppercase tracking-wider text-primary hover:bg-primary/10 disabled:opacity-40"
          >
            {t("stark.tasks.save")}
          </button>
        </form>
      )}

      <ul className="divide-y divide-primary/10" data-testid="stark-tasks-list">
        {live && (
          <TaskRow
            testId="stark-task-live"
            title={t(live.current.labelKey)}
            sub={live.current.detail || t("stark.tasks.live")}
            progress={live.done / live.total}
            spinning
          />
        )}
        {tasks.map((task) => (
          <TaskRow
            key={task.id}
            testId="stark-task-scheduled"
            title={task.title}
            sub={task.state === "running" ? t("stark.tasks.running") : dueLine(task, t)}
            spinning={task.state === "running"}
          />
        ))}
        {todos.map((todo) => (
          <li key={todo.id} className="group flex items-center gap-3 py-2" data-testid="stark-todo" data-done={todo.done ? "true" : "false"}>
            <button
              type="button"
              role="checkbox"
              aria-checked={todo.done}
              aria-label={t(todo.done ? "stark.tasks.reopen" : "stark.tasks.complete")}
              title={t(todo.done ? "stark.tasks.reopen" : "stark.tasks.complete")}
              onClick={() => toggle.mutate({ id: todo.id, done: !todo.done })}
              data-testid="stark-todo-check"
              className={cn(
                "flex h-5 w-5 shrink-0 items-center justify-center rounded-full border transition-colors",
                todo.done
                  ? "border-[hsl(var(--stark-ok))] bg-[hsl(var(--stark-ok))] text-background"
                  : "border-foreground/50 hover:border-primary",
              )}
            >
              {todo.done && <Check className="h-3 w-3" strokeWidth={3} aria-hidden />}
            </button>
            <div className="min-w-0 flex-1">
              <p className={cn("truncate font-sans text-sm text-foreground", todo.done && "text-muted-foreground line-through")}>
                {todo.title}
              </p>
              <p className="truncate text-xs text-muted-foreground">
                {todo.done ? t("stark.tasks.done") : t("stark.tasks.todo")}
              </p>
            </div>
            <button
              type="button"
              onClick={() => dismiss.mutate(todo.id)}
              aria-label={t("stark.tasks.dismiss")}
              title={t("stark.tasks.dismiss")}
              data-testid="stark-todo-dismiss"
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-muted-foreground opacity-60 hover:bg-primary/10 hover:text-[hsl(var(--stark-hot))] hover:opacity-100 focus-visible:opacity-100"
            >
              <X className="h-3.5 w-3.5" aria-hidden />
            </button>
          </li>
        ))}
      </ul>

      {todosQ.isError && (
        <p role="alert" className="py-2 text-xs text-[hsl(var(--stark-hot))]" data-testid="stark-tasks-error">
          {t("stark.tasks.error")}
        </p>
      )}
      {!todosQ.isError && todosQ.isSuccess && open === 0 && todos.length === 0 && (
        <p className="py-3 text-sm text-muted-foreground" data-testid="stark-tasks-empty">
          {t("stark.tasks.empty")}
        </p>
      )}
      {todosQ.isPending && (
        <p className="py-3 text-sm text-muted-foreground">{t("common.loading")}</p>
      )}
    </StarkPanel>
  );
}

function TaskRow({
  title,
  sub,
  progress,
  spinning,
  testId,
}: {
  title: string;
  sub: string;
  progress?: number;
  spinning?: boolean;
  testId: string;
}) {
  return (
    <li className="flex items-center gap-3 py-2" data-testid={testId}>
      <span aria-hidden className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-primary/60">
        <span className="h-1.5 w-1.5 rounded-full bg-primary" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate font-sans text-sm text-foreground">{title}</p>
        <p className="truncate text-xs text-muted-foreground">{sub}</p>
      </div>
      {progress !== undefined && (
        <span className="text-xs tabular-nums text-muted-foreground">{Math.round(progress * 100)}%</span>
      )}
      <ProgressRing value={progress ?? null} spinning={spinning} />
    </li>
  );
}

/** A small ring: filled to `value`, or a turning arc while the length is unknown. */
function ProgressRing({ value, spinning }: { value: number | null; spinning?: boolean }) {
  const r = 9;
  const c = 2 * Math.PI * r;
  const v = value ?? 0.25;
  return (
    <svg width={24} height={24} viewBox="0 0 24 24" aria-hidden className={cn(value === null && spinning && "animate-spin motion-reduce:animate-none")}>
      <circle cx={12} cy={12} r={r} fill="none" stroke="hsl(var(--primary) / 0.2)" strokeWidth={2} />
      <circle
        cx={12}
        cy={12}
        r={r}
        fill="none"
        stroke="hsl(var(--primary))"
        strokeWidth={2}
        strokeLinecap="round"
        strokeDasharray={`${c * v} ${c}`}
        transform="rotate(-90 12 12)"
      />
    </svg>
  );
}

function dueLine(task: TaskSummary, t: (k: string) => string): string {
  const ns = task.next_due_at_ns ?? task.due_at_ns;
  if (!ns) return t("stark.tasks.scheduled");
  const at = new Date(ns / 1e6);
  return `${t("stark.tasks.due")} ${at.toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" })}`;
}

