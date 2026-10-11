/**
 * The HUD memory panel's client for the project memory (`/api/brain-map/*`)
 * and the wiki's search and pages (`/api/wiki/*`).
 *
 * Read-only on purpose: the panel inspects and jumps, it never edits a
 * project. Typed to the fields the panel draws and nothing more, so a field
 * the backend adds later never breaks the HUD. Every reader throws on a
 * failed request; the panel turns that into "not reachable", never into an
 * empty list that would look like "no memory".
 */

export type MemoryStatus = "red" | "blue" | "yellow" | "green";

export type MemoryKind =
  | "PROJECT"
  | "CONVERSATION"
  | "THREAD"
  | "TASK"
  | "DECISION"
  | "BLOCKER"
  | "MILESTONE"
  | "DOCUMENT"
  | "AGENT"
  | "OUTPUT";

export interface MemoryEntity {
  id: string;
  kind: MemoryKind;
  title: string;
  status: MemoryStatus;
  status_reason: string;
  last_activity_ms: number;
  priority_score: number;
  priority_reason: string;
  source?: { source_kind: string; source_id: string; locator?: string };
}

export interface MemoryProject extends MemoryEntity {
  summary: string;
  counts: {
    conversations: number;
    open_tasks: number;
    open_blockers: number;
    decisions: number;
    suggested: number;
  };
}

export interface MemoryProjectDetail extends MemoryProject {
  tasks: MemoryEntity[];
  blockers: MemoryEntity[];
  decisions: MemoryEntity[];
  milestones: MemoryEntity[];
  next_actions: { action: string; entity_id: string; why: string }[];
}

export interface MemoryEntityDetail extends MemoryEntity {
  summary?: string;
  evidence: { quote: string; source: { source_kind: string; locator?: string } }[];
  relations: { outgoing: unknown[]; incoming: unknown[] };
  projects: string[];
}

export interface MemoryGraphNode extends MemoryEntity {
  suggested?: boolean;
}

export interface WikiHit {
  slug: string;
  title: string;
  path: string;
  snippet: string;
  score: number;
}

export interface WikiPage {
  slug: string;
  title: string;
  path: string;
  kind: string;
  body_md: string;
  wikilinks: string[];
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { cache: "no-store", signal });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as T;
}

export async function fetchMemoryProjects(signal?: AbortSignal): Promise<MemoryProject[]> {
  const body = await getJson<{ projects?: MemoryProject[] }>("/api/brain-map/projects", signal);
  return Array.isArray(body.projects) ? body.projects : [];
}

export function fetchMemoryProject(id: string, signal?: AbortSignal): Promise<MemoryProjectDetail> {
  return getJson<MemoryProjectDetail>(`/api/brain-map/projects/${encodeURIComponent(id)}`, signal);
}

export function fetchMemoryEntity(id: string, signal?: AbortSignal): Promise<MemoryEntityDetail> {
  return getJson<MemoryEntityDetail>(`/api/brain-map/entities/${encodeURIComponent(id)}`, signal);
}

/** The memory's items as one list — what the search box filters by title. */
export async function fetchMemoryNodes(signal?: AbortSignal): Promise<MemoryGraphNode[]> {
  const body = await getJson<{ nodes?: MemoryGraphNode[] }>("/api/brain-map/graph?limit=1500", signal);
  return Array.isArray(body.nodes) ? body.nodes : [];
}

/** Keyword search over the vault. `{ok:false}` for an empty query is no hits. */
export async function searchWiki(q: string, signal?: AbortSignal): Promise<WikiHit[]> {
  const body = await getJson<{ ok?: boolean; hits?: WikiHit[]; error?: string }>(
    `/api/wiki/search?q=${encodeURIComponent(q)}&k=8`,
    signal,
  );
  if (body.ok === false) {
    if (body.error === "empty query") return [];
    throw new Error(body.error || "search failed");
  }
  return Array.isArray(body.hits) ? body.hits : [];
}

export async function fetchWikiPage(slug: string, signal?: AbortSignal): Promise<WikiPage> {
  const body = await getJson<WikiPage & { ok?: boolean; error?: string }>(
    `/api/wiki/page/${encodeURIComponent(slug)}`,
    signal,
  );
  if (body.ok === false) throw new Error(body.error || "page not found");
  return body;
}

/**
 * Items whose title holds every word of the query, best first: a title that
 * starts with the query, then by the memory's own priority. Projects are left
 * out — they have their own list right above.
 */
export function matchMemoryNodes(nodes: MemoryGraphNode[], query: string, limit = 8): MemoryGraphNode[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [];
  const q = words.join(" ");
  return nodes
    .filter((n) => n.kind !== "PROJECT")
    .filter((n) => {
      const title = n.title.toLowerCase();
      return words.every((w) => title.includes(w));
    })
    .sort((a, b) => {
      const pa = a.title.toLowerCase().startsWith(q) ? 1 : 0;
      const pb = b.title.toLowerCase().startsWith(q) ? 1 : 0;
      if (pa !== pb) return pb - pa;
      return (b.priority_score ?? 0) - (a.priority_score ?? 0);
    })
    .slice(0, limit);
}

/** Projects whose title or summary holds every word of the query. */
export function matchProjects(projects: MemoryProject[], query: string): MemoryProject[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return projects;
  return projects.filter((p) => {
    const hay = `${p.title} ${p.summary ?? ""}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  });
}

/** The first lines of a page's markdown as plain text, for a preview. */
export function previewMarkdown(md: string, max = 700): string {
  const text = md
    .replace(/^---[\s\S]*?---\s*/, "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, "$2")
    .replace(/\[\[([^\]]+)\]\]/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/[*_`>]/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return text.length > max ? `${text.slice(0, max).trimEnd()}…` : text;
}
