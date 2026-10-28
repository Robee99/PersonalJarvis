/**
 * The brain map as the Orb reads it: types for `/api/brain-map/*` and the
 * fetchers. The backend (`jarvis/memory/brain_map`) is the only place that
 * decides anything; this file names its vocabulary and draws nothing.
 *
 * `HEALTH_RULE_IDS` and `BRANCH_KINDS` mirror `health.py` / `thread.py`;
 * `tests/unit/memory/brain_map/test_health.py` reads this file and fails when
 * they drift. The health *rule* is never re-implemented here: a project's
 * colour is whatever `project.health.colour` says, and the legend lists the
 * rule table the API serves with every project list.
 */

export type StatusColour = "red" | "yellow" | "blue" | "green";

export const HEALTH_RULE_IDS = [
  "blocked",
  "failed",
  "verified",
  "stalled",
  "partial",
  "review",
  "active",
  "idle",
] as const;
export type HealthRuleId = (typeof HEALTH_RULE_IDS)[number];

export const BRANCH_KINDS = [
  "topic",
  "decision",
  "experiment",
  "subtask",
  "blocker",
  "milestone",
  "artifact",
] as const;
export type BranchKind = (typeof BRANCH_KINDS)[number];

export type EntityKind =
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

/** Display order of the colours, worst first: what needs a look comes first. */
export const HEALTH_COLOURS: StatusColour[] = ["red", "yellow", "blue", "green"];

/** HUD palette per colour: a bright stroke for a dark canvas and a soft glow. */
export const HEALTH_HEX: Record<StatusColour, string> = {
  red: "#ff5d6c",
  yellow: "#ffc145",
  blue: "#4f9dff",
  green: "#3ddc97",
};

export interface SourceRef {
  source_kind: string;
  source_id: string;
  conversation_id: string;
  api: string | null;
  locator: string;
}

export interface Entity {
  id: string;
  kind: EntityKind;
  title: string;
  status: StatusColour;
  status_reason: string;
  confidence: number;
  origin: "native" | "extracted" | "derived";
  last_activity_ms: number;
  last_updated_ms: number;
  priority_score: number;
  priority_reason: string;
  assignment: "confirmed" | "suggested";
  source: SourceRef;
}

export interface Health {
  colour: StatusColour;
  rule: HealthRuleId;
  reason: string;
  /** Done share of tracked work, 0..1; null when nothing is tracked. */
  progress: number | null;
  done: number;
  total: number;
  open_blockers: number;
  open_tasks: number;
}

export interface HealthRule {
  id: HealthRuleId;
  colour: StatusColour;
  when: string;
}

export interface Project extends Entity {
  summary: string;
  counts: {
    conversations: number;
    open_tasks: number;
    open_blockers: number;
    decisions: number;
    suggested: number;
  };
  locator: string;
  health: Health;
}

export interface ProjectsResponse {
  projects: Project[];
  last_run: { finished_ms: number; errors: number } | null;
  health_rules: HealthRule[];
}

export interface ThreadBranch {
  id: string;
  kind: EntityKind;
  branch: BranchKind;
  title: string;
  status: StatusColour;
  status_reason: string;
  assignment: "confirmed" | "suggested";
  first_seen_ms: number;
  last_activity_ms: number;
  source: SourceRef;
  children: ThreadBranch[];
  hidden_children: number;
}

export interface ProjectThread {
  project_id: string;
  branches: ThreadBranch[];
  hidden_branches: number;
  counts: Record<BranchKind, number>;
}

export interface ProjectDetail extends Project {
  timeline: {
    ts_ms: number;
    entity_id: string;
    kind: EntityKind;
    title: string;
    status: StatusColour | null;
    reason: string;
    event: "status" | "said";
  }[];
  conversations: Entity[];
  decisions: Entity[];
  tasks: Entity[];
  blockers: Entity[];
  milestones: Entity[];
  files: { kind: string; path: string; entity_id?: string | null }[];
  branches: { name: string; path: string; source: string }[];
  agents: Entity[];
  reports: Entity[];
  next_actions: { action: string; entity_id: string; why: string }[];
  priority: { score: number; reason: string };
  thread: ProjectThread;
}

export interface Evidence {
  quote: string;
  confidence: number;
  extractor: string;
  source: SourceRef & { message_ref: string; message_id: string; ts_ms: number };
}

export interface EntityDetail extends Entity {
  evidence: Evidence[];
  relations: {
    outgoing: { kind: string; target: string; assignment: string; basis: string }[];
    incoming: { kind: string; source: string; assignment: string; basis: string }[];
  };
  status_history: { ts_ms: number; status: StatusColour; reason: string }[];
  projects: string[];
  summary: string;
}

/** One message of a conversation, as the branch panel shows it. */
export interface BranchMessage {
  seq: number;
  ts_ms: number;
  role: "user" | "assistant";
  text: string;
}

export class BrainMapError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) throw new BrainMapError(`${url}: HTTP ${res.status}`, res.status);
  return (await res.json()) as T;
}

export const fetchProjects = () => getJson<ProjectsResponse>("/api/brain-map/projects");

export const fetchProject = (id: string) =>
  getJson<ProjectDetail>(`/api/brain-map/projects/${encodeURIComponent(id)}`);

export const fetchEntity = (id: string) =>
  getJson<EntityDetail>(`/api/brain-map/entities/${encodeURIComponent(id)}`);

export const reindexBrainMap = () => getJson<unknown>("/api/brain-map/reindex", { method: "POST" });

/** Newest messages of an agent-chat conversation, oldest first. */
export const MESSAGE_TAIL = 40;

interface AgentChatEvent {
  seq: number;
  ts_ms: number;
  kind: string;
  payload: { text?: string };
}

export function messagesFromEvents(events: AgentChatEvent[]): BranchMessage[] {
  const out: BranchMessage[] = [];
  for (const event of events) {
    const text = (event.payload?.text ?? "").trim();
    if (!text) continue;
    if (event.kind === "user_message") out.push({ seq: event.seq, ts_ms: event.ts_ms, role: "user", text });
    else if (event.kind === "assistant_text" || event.kind === "agent_message")
      out.push({ seq: event.seq, ts_ms: event.ts_ms, role: "assistant", text });
  }
  return out;
}

/**
 * The conversation behind a branch, when its source is an agent-chat session
 * (the only source with a message API). Anything else shows its evidence
 * quotes instead, which are the person's own words, verbatim.
 */
export async function fetchBranchMessages(source: SourceRef): Promise<BranchMessage[] | null> {
  if (source.source_kind !== "agent_chat" || !source.api) return null;
  const body = await getJson<{ events: AgentChatEvent[] }>(`${source.api}?tail=${MESSAGE_TAIL}`);
  return messagesFromEvents(body.events ?? []);
}

/** `YYYY-MM-DD` in UTC, the same day format the backend's reasons use. */
export function isoDay(ms: number): string {
  if (!ms) return "";
  return new Date(ms).toISOString().slice(0, 10);
}
