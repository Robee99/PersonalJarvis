import { useQuery, useQueryClient } from "@tanstack/react-query";

/**
 * The server-side focus session (jarvis/focus/session.py) as the rail sees
 * it. The session lives on the server, so voice ("thirty minutes on this")
 * and the FOCUS button drive the same one; the rail only reads and asks.
 */
export interface FocusState {
  active: boolean;
  goal: string;
  minutes: number;
  remaining_s: number;
  target: string;
  lock: "app" | "tab" | "";
  deferred: boolean;
  drifts: number;
  phone_drifts: number;
  on_target_percent: number;
}

const KEY = ["focus", "state"];

async function readFocus(): Promise<FocusState> {
  const response = await fetch("/api/focus");
  if (!response.ok) throw new Error(`Focus state unavailable (${response.status})`);
  return (await response.json()) as FocusState;
}

async function post(path: string, body?: unknown): Promise<FocusState & { text: string }> {
  const response = await fetch(`/api/focus/${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(detail.detail || `Focus request failed (${response.status})`);
  }
  return (await response.json()) as FocusState & { text: string };
}

export function useFocusSession() {
  const client = useQueryClient();
  const query = useQuery<FocusState>({
    queryKey: KEY,
    queryFn: readFocus,
    // A running session ticks on the server; the rail's clock follows it.
    refetchInterval: (q) => (q.state.data?.active ? 1_000 : 10_000),
    retry: false,
  });
  const run = async (path: "start" | "stop" | "lock", body?: unknown) => {
    const next = await post(path, body);
    // A read still in flight carries the state from before this change.
    await client.cancelQueries({ queryKey: KEY });
    client.setQueryData(KEY, next);
    return next;
  };
  return {
    state: query.data,
    error: query.error,
    start: (minutes = 30, goal = "") => run("start", { minutes, goal }),
    stop: () => run("stop"),
    lock: () => run("lock"),
  };
}

/** "24:05" from seconds. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
