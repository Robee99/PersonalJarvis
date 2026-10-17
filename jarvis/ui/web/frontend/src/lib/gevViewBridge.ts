const VIEW_EVENT = "jarvis:gev-view";
const PENDING_VIEW_KEY = "jarvis.pending-gev-view.v1";
const PENDING_TTL_MS = 10 * 60 * 1000;
const MAX_OUTPUT_CHARS = 100_000;

type GeVView = Record<string, unknown> & { camera: Record<string, unknown> };

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function parseJson(value: string): unknown {
  if (value.length > MAX_OUTPUT_CHARS) return null;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return null;
  }
}

function extractView(value: unknown, depth = 0): GeVView | null {
  if (depth > 5 || !isRecord(value)) return null;
  const candidate = value.view;
  if (isRecord(candidate) && isRecord(candidate.camera))
    return candidate as GeVView;

  for (const key of ["data", "structuredContent", "result", "content"]) {
    const nested = value[key];
    if (Array.isArray(nested)) {
      for (const item of nested) {
        if (isRecord(item) && typeof item.text === "string") {
          const found = extractView(parseJson(item.text), depth + 1);
          if (found) return found;
        }
      }
      continue;
    }
    const found = extractView(nested, depth + 1);
    if (found) return found;
  }
  return null;
}

function isGeVTool(name: string): boolean {
  return /(?:^|[/:._])show_in_gods_eye_view$/i.test(name.trim());
}

function publish(view: GeVView, callId: string): void {
  const detail = { view, callId, at: Date.now() };
  try {
    sessionStorage.setItem(PENDING_VIEW_KEY, JSON.stringify(detail));
  } catch {
    // The mounted Command Center can still receive the event if storage is blocked.
  }
  window.dispatchEvent(new CustomEvent(VIEW_EVENT, { detail }));
}

/** Forward only the successful result of GEV's own approved MCP tool. */
export function publishGeVViewResult(
  toolName: string,
  output: string | null,
  callId: string,
): void {
  if (!output || !isGeVTool(toolName)) return;
  const view = extractView(parseJson(output));
  if (view) publish(view, callId);
}

/** Read a fresh command if the globe workspace was not mounted yet. */
export function readPendingGeVView(): GeVView | null {
  try {
    const raw = sessionStorage.getItem(PENDING_VIEW_KEY);
    if (!raw) return null;
    const detail = JSON.parse(raw) as { view?: unknown; at?: unknown };
    if (
      typeof detail.at !== "number" ||
      Date.now() - detail.at > PENDING_TTL_MS ||
      !isRecord(detail.view) ||
      !isRecord(detail.view.camera)
    ) {
      sessionStorage.removeItem(PENDING_VIEW_KEY);
      return null;
    }
    return detail.view as GeVView;
  } catch {
    return null;
  }
}

/** Remove a delivered command so remounting the workspace does not replay it. */
export function clearPendingGeVView(): void {
  try {
    sessionStorage.removeItem(PENDING_VIEW_KEY);
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
}
