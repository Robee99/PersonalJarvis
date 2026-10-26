// "Import Data" on the memory orb: bring a folder of notes — an Obsidian vault,
// a folder like C:\AI DATA, or one file — into the wiki the assistant recalls
// from. The server does the work (`/api/wiki/import`, `jarvis/memory/wiki/
// importer.py`); this panel picks the source, starts the job, shows its
// progress and refreshes the orb when it ends.
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { FileArchive, FolderOpen, Keyboard, Loader2, Trash2, Upload, X } from "lucide-react";

import { cn } from "@/lib/utils";

/** One import's progress, as `GET /api/wiki/import/{id}` reports it. */
export interface ImportState {
  job_id: string;
  running?: boolean;
  phase: string;
  source: string;
  obsidian_vault: boolean;
  discovered: number;
  supported: number;
  imported: number;
  updated: number;
  unchanged: number;
  skipped: number;
  failed: number;
  current: string;
  skip_reasons: Record<string, number>;
  /** "<extension>: <reason>" → files, for the files that gave no page. */
  skipped_types?: Record<string, number>;
  /** Pages made from AI chat exports (one per conversation). */
  conversations?: number;
  problems: { path: string; reason: string }[];
  error: string;
}

async function detail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}`;
  }
}

/** A one-line summary of where an import stands. */
export function importSummary(s: ImportState): string {
  const parts = [`${s.imported} new`, `${s.updated} updated`, `${s.unchanged} unchanged`];
  if (s.conversations) parts.push(`${s.conversations} conversation pages`);
  if (s.skipped) parts.push(`${s.skipped} skipped`);
  if (s.failed) parts.push(`${s.failed} failed`);
  const counts = parts.join(" · ");
  switch (s.phase) {
    case "done":
      return `Done: ${counts}.`;
    case "cancelled":
      return `Stopped: ${counts}. Import again to finish.`;
    case "failed":
      return `Failed: ${s.error || "unknown error"}. ${counts}.`;
    default:
      return `Importing ${s.discovered} files so far: ${counts}.`;
  }
}

export function MemoryOrbImport({ defaultOpen = false }: { defaultOpen?: boolean } = {}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(defaultOpen);
  const [path, setPath] = useState("");
  const [state, setState] = useState<ImportState | null>(null);
  const [error, setError] = useState("");
  const [picking, setPicking] = useState(false);
  const running = Boolean(state?.running);
  const [wiping, setWiping] = useState(false);
  const [wiped, setWiped] = useState("");
  // The path box stays hidden: the pickers are the way in. Typing is the fallback.
  const [typing, setTyping] = useState(false);

  // Poll the running job; refresh the orb once it ends.
  useEffect(() => {
    if (!state?.job_id || !running) return;
    const id = window.setInterval(async () => {
      try {
        const res = await fetch(`/api/wiki/import/${state.job_id}`);
        if (!res.ok) return;
        const next = (await res.json()) as ImportState;
        setState(next);
        if (!next.running) {
          void queryClient.invalidateQueries({ queryKey: ["orb", "sources"] });
        }
      } catch {
        // A missed poll is retried on the next tick.
      }
    }, 800);
    return () => window.clearInterval(id);
  }, [state?.job_id, running, queryClient]);

  // Live 2026-10-26: a folder dialog never shows .json or .zip files, so a
  // ChatGPT export could not be picked. One button per kind; a pick imports.
  async function pick(kind: "folder" | "file") {
    setPicking(true);
    setError("");
    try {
      const res = await fetch(`/api/wiki/import/pick-${kind}`, { method: "POST" });
      if (!res.ok) {
        setError(`${await detail(res)} Type or paste the path instead.`);
        setTyping(true);
        return;
      }
      const body = (await res.json()) as { path?: string; cancelled?: boolean };
      if (body.path) {
        setPath(body.path);
        await start(body.path);
      }
    } finally {
      setPicking(false);
    }
  }

  async function start(source: string = path) {
    setError("");
    const res = await fetch("/api/wiki/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: source.trim() }),
    });
    if (!res.ok) {
      setError(await detail(res));
      return;
    }
    setState({ ...((await res.json()) as ImportState), running: true });
  }

  async function wipe() {
    setError("");
    setWiped("");
    const res = await fetch("/api/wiki/wipe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // The server still wants the phrase; the second click is the person's yes.
      body: JSON.stringify({ confirm: "DELETE ALL NOTES" }),
    });
    if (!res.ok) {
      setError(await detail(res));
      return;
    }
    const body = (await res.json()) as { pages: number; backup: string };
    setWiped(`Deleted ${body.pages} notes. A copy of everything is in ${body.backup}.`);
    setWiping(false);
    void queryClient.invalidateQueries({ queryKey: ["orb"] });
    void queryClient.invalidateQueries({ queryKey: ["orb2"] });
  }

  async function cancel() {
    if (state?.job_id) await fetch(`/api/wiki/import/${state.job_id}/cancel`, { method: "POST" });
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="pointer-events-auto flex h-9 items-center gap-1.5 rounded-full border border-cyan-400/20 bg-black/50 px-3 text-xs text-slate-200 backdrop-blur hover:border-cyan-300/60"
      >
        <Upload className="h-3.5 w-3.5" aria-hidden /> Import Data
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Import data into memory"
          className="pointer-events-auto absolute right-4 top-16 z-10 w-96 rounded-xl border border-cyan-400/20 bg-black/80 p-4 text-xs text-slate-200 backdrop-blur"
        >
          <div className="mb-3 flex items-center">
            <p className="font-mono text-[10px] uppercase tracking-[0.25em] text-cyan-200/90">Import data</p>
            <button type="button" aria-label="Close" onClick={() => setOpen(false)} className="ml-auto">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <p className="mb-2 text-slate-400">
            A folder of notes, an Obsidian vault, one file or a .zip. Notes, documents (PDF, Word,
            slides, spreadsheets, web pages) and AI chat exports (the ChatGPT or Claude export .zip as
            downloaded, or its JSON files) are added to memory; re-importing only picks up what
            changed.
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void pick("file")}
              disabled={picking || running}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-md bg-cyan-500/80 px-2 py-1.5 font-medium text-black hover:bg-cyan-400 disabled:opacity-40"
            >
              <FileArchive className="h-3.5 w-3.5" aria-hidden /> Choose file (.zip, .json)
            </button>
            <button
              type="button"
              onClick={() => void pick("folder")}
              disabled={picking || running}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-md border border-white/10 px-2 py-1.5 hover:border-cyan-300/60 disabled:opacity-40"
            >
              <FolderOpen className="h-3.5 w-3.5" aria-hidden /> Choose folder
            </button>
          </div>
          {path && !typing && <p className="mt-2 truncate text-slate-400">{path}</p>}
          {typing ? (
            <div className="mt-2 flex gap-2">
              <input
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder="C:\AI DATA"
                aria-label="Folder or file to import"
                className="min-w-0 flex-1 rounded-md border border-white/10 bg-black/50 px-2 py-1.5 outline-none focus:border-cyan-300/60"
              />
              <button
                type="button"
                onClick={() => void start()}
                disabled={!path.trim() || running}
                className="rounded-md bg-cyan-500/80 px-3 py-1.5 font-medium text-black hover:bg-cyan-400 disabled:opacity-40"
              >
                Import
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setTyping(true)}
              className="mt-2 flex items-center gap-1 text-slate-500 hover:text-slate-300"
            >
              <Keyboard className="h-3 w-3" aria-hidden /> Type a path instead
            </button>
          )}
          {running && (
            <button
              type="button"
              onClick={() => void cancel()}
              className="mt-2 rounded-md border border-white/10 px-3 py-1.5 hover:border-red-300/60"
            >
              Cancel
            </button>
          )}
          <div className="mt-4 border-t border-white/10 pt-3">
            {!wiping ? (
              <button
                type="button"
                onClick={() => setWiping(true)}
                disabled={running}
                className="flex items-center gap-1.5 text-red-300/80 hover:text-red-300 disabled:opacity-40"
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden /> Delete all notes
              </button>
            ) : (
              <div className="space-y-2">
                <p className="text-slate-300">
                  Delete every note in memory and empty its search? A full copy is saved first.
                </p>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => void wipe()}
                    disabled={running}
                    className="rounded-md bg-red-500/80 px-3 py-1.5 font-medium text-black hover:bg-red-400 disabled:opacity-40"
                  >
                    Yes, delete all notes
                  </button>
                  <button
                    type="button"
                    onClick={() => setWiping(false)}
                    className="rounded-md border border-white/10 px-2 hover:border-cyan-300/60"
                  >
                    Keep them
                  </button>
                </div>
              </div>
            )}
            {wiped && (
              <p role="status" className="mt-2 text-slate-300">
                {wiped}
              </p>
            )}
          </div>
          {error && (
            <p role="alert" className="mt-3 text-red-300">
              {error}
            </p>
          )}
          {state && (
            <div className="mt-3 space-y-1" role="status" aria-live="polite">
              <p className={cn("flex items-center gap-1.5", state.phase === "failed" && "text-red-300")}>
                {running && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
                {importSummary(state)}
              </p>
              {state.obsidian_vault && <p className="text-slate-400">Obsidian vault detected.</p>}
              {running && state.current && <p className="truncate text-slate-500">{state.current}</p>}
              {Object.entries(state.skipped_types ?? {})
                .slice(0, 6)
                .map(([what, n]) => (
                  <p key={what} className="truncate text-slate-400">
                    Skipped {n} × {what}
                  </p>
                ))}
              {state.problems.slice(0, 5).map((p) => (
                <p key={p.path} className="truncate text-slate-500">
                  {p.path}: {p.reason}
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  );
}
