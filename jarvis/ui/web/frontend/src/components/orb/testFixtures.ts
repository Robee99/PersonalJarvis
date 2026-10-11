/** Brain-map shapes for the Orb's tests: a project and a thread branch. */
import type { Project, ThreadBranch } from "./brainMap";

export function project(id: string, over: Partial<Project> & { colour?: Project["health"]["colour"]; progress?: number | null } = {}): Project {
  const { colour = "blue", progress = null, ...rest } = over;
  return {
    id,
    kind: "PROJECT",
    title: id,
    status: colour,
    status_reason: "",
    confidence: 1,
    origin: "derived",
    last_activity_ms: 0,
    last_updated_ms: 0,
    priority_score: 0,
    priority_reason: "",
    assignment: "confirmed",
    source: { source_kind: "ide_project", source_id: id, conversation_id: "", api: null, locator: "" },
    summary: "",
    counts: { conversations: 0, open_tasks: 0, open_blockers: 0, decisions: 0, suggested: 0 },
    locator: "",
    health: {
      colour,
      rule: "active",
      reason: `${colour} reason`,
      progress,
      done: 0,
      total: 0,
      open_blockers: 0,
      open_tasks: 0,
    },
    ...rest,
  };
}

export function branch(id: string, kind: ThreadBranch["branch"], children: ThreadBranch[] = [], last = 0): ThreadBranch {
  return {
    id,
    kind: kind === "topic" ? "CONVERSATION" : "TASK",
    branch: kind,
    title: id,
    status: "blue",
    status_reason: "",
    assignment: "confirmed",
    first_seen_ms: last,
    last_activity_ms: last,
    source: { source_kind: "agent_chat", source_id: id, conversation_id: id, api: `/api/agent-chat/sessions/${id}`, locator: "" },
    children,
    hidden_children: 0,
  };
}
