/** Shared API for discovering and selecting a model server on this computer. */

export interface LocalServer {
  url: string;
  port: number;
  kind: string;
  state: "running" | "loading" | "off";
  models: string[];
  hermes_provider: string;
  hermes_model: string;
  context?: number | null;
}

export interface LocalServersResult {
  hermes_brain: boolean;
  selection: string;
  servers: LocalServer[];
  hermes_min_context?: number;
}

export interface LocalServerUseResult {
  ok: boolean;
  brain: string;
  selection: string;
  server_state: LocalServer["state"];
  applied_live: boolean;
}

export async function listLocalServers(): Promise<LocalServersResult> {
  const res = await fetch("/api/local-servers");
  const body = (await res.json().catch(() => ({}))) as Partial<LocalServersResult> & { detail?: unknown };
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
  return {
    hermes_brain: Boolean(body.hermes_brain),
    selection: typeof body.selection === "string" ? body.selection : "",
    servers: Array.isArray(body.servers) ? body.servers : [],
    hermes_min_context: body.hermes_min_context,
  };
}

export async function useLocalServer(
  url: string,
  model: string,
  direct = false,
): Promise<LocalServerUseResult> {
  const res = await fetch("/api/local-servers/use", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(direct ? { url, model, direct: true } : { url, model }),
  });
  const body = (await res.json().catch(() => ({}))) as Partial<LocalServerUseResult> & { detail?: unknown };
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
  return body as LocalServerUseResult;
}
