import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "@/lib/utils";

type JarvisServer = {
  name: string;
  display?: string;
  description?: string;
  enabled: boolean;
  status: string;
  error?: string | null;
  tools?: { name: string }[];
};

type HermesServer = { name: string; transport: string; enabled: boolean };
type CatalogEntry = { name: string; description: string; auth: string; installed: boolean };

type CatalogueData = {
  jarvis: JarvisServer[];
  jarvisError: string | null;
  hermes: HermesServer[];
  hermesError: string | null;
};

type HermesCatalog = { available: boolean; error: string | null; mcp_servers: CatalogEntry[] };

type CatalogueRow = {
  key: string;
  name: string;
  description: string;
  jarvis?: JarvisServer;
  hermes?: HermesServer;
  catalog?: CatalogEntry;
};

type Action =
  | { source: "jarvis"; name: string; enable: boolean }
  | { source: "hermes"; name: string; enable: boolean }
  | { source: "catalog"; name: string };

type ActionOutcome = { restart_needed?: boolean };

async function readJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as T;
}

async function fetchCatalogue(refreshHermes = false): Promise<CatalogueData> {
  const hermesUrl = refreshHermes ? "/api/hermes/inventory?refresh=true" : "/api/hermes/inventory";
  const [jarvisResult, hermesResult] = await Promise.allSettled([
    readJson<{ servers?: JarvisServer[] }>("/api/mcps"),
    readJson<{ mcp_servers?: HermesServer[] }>(hermesUrl),
  ]);
  return {
    jarvis: jarvisResult.status === "fulfilled" ? jarvisResult.value.servers ?? [] : [],
    jarvisError: jarvisResult.status === "rejected" ? String(jarvisResult.reason) : null,
    hermes: hermesResult.status === "fulfilled" ? hermesResult.value.mcp_servers ?? [] : [],
    hermesError: hermesResult.status === "rejected" ? String(hermesResult.reason) : null,
  };
}

async function fetchCatalog(): Promise<HermesCatalog> {
  return readJson<HermesCatalog>("/api/hermes/catalog");
}

function identity(name: string): string {
  return name.trim().toLocaleLowerCase("en-US");
}

export function mergeMcpCatalogue(
  data: CatalogueData | undefined,
  catalog: HermesCatalog | undefined,
): CatalogueRow[] {
  const rows = new Map<string, CatalogueRow>();
  const get = (name: string) => {
    const key = identity(name);
    let row = rows.get(key);
    if (!row) {
      row = { key, name, description: "" };
      rows.set(key, row);
    }
    return row;
  };

  for (const server of data?.jarvis ?? []) {
    const row = get(server.name);
    row.jarvis = server;
    row.name = server.display || row.name;
    row.description ||= server.description ?? "";
  }
  for (const server of data?.hermes ?? []) {
    const row = get(server.name);
    row.hermes = server;
    row.description ||= server.transport ? `Hermes transport: ${server.transport}` : "";
  }
  for (const entry of catalog?.mcp_servers ?? []) {
    const row = get(entry.name);
    row.catalog = entry;
    row.description ||= entry.description;
  }

  return [...rows.values()].sort((a, b) => a.name.localeCompare(b.name));
}

async function runAction(action: Action): Promise<ActionOutcome> {
  const url =
    action.source === "jarvis"
      ? `/api/mcps/${encodeURIComponent(action.name)}/${action.enable ? "enable" : "disable"}`
      : "/api/hermes/action";
  const body =
    action.source === "jarvis"
      ? undefined
      : action.source === "catalog"
        ? { kind: "mcp_catalog", op: "install", name: action.name }
        : { kind: "mcp", op: action.enable ? "enable" : "disable", name: action.name };
  const response = await fetch(url, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const result = (await response.json().catch(() => ({}))) as {
    ok?: boolean;
    error?: string;
    detail?: string;
    restart_needed?: boolean;
  };
  if (!response.ok || result.ok === false) {
    throw new Error(result.error || result.detail || `HTTP ${response.status}`);
  }
  return { restart_needed: result.restart_needed };
}

export function ArmoryMcpCatalogue() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState("");
  const [scope, setScope] = useState<"all" | "configured" | "available">("all");
  const [showCatalog, setShowCatalog] = useState(false);
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const inventory = useQuery({
    queryKey: ["armory", "mcp-catalogue"],
    queryFn: () => fetchCatalogue(),
    staleTime: 10_000,
  });
  const catalog = useQuery({
    queryKey: ["armory", "mcp-catalogue", "available"],
    queryFn: fetchCatalog,
    enabled: showCatalog,
    staleTime: 60_000,
    retry: false,
  });
  const action = useMutation({
    mutationFn: runAction,
    onMutate: () => setActionNotice(null),
    onSuccess: (result) => {
      setActionNotice(result.restart_needed ? "Saved. Restart Hermes to load this MCP." : "MCP setting updated.");
    },
    onSettled: (_result, _error, variables) => {
      if (variables.source === "jarvis") {
        void queryClient.invalidateQueries({ queryKey: ["armory", "mcp-catalogue"] });
      } else {
        void queryClient.fetchQuery({
          queryKey: ["armory", "mcp-catalogue"],
          queryFn: () => fetchCatalogue(true),
          staleTime: 0,
        }).catch(() => undefined);
      }
      void queryClient.invalidateQueries({ queryKey: ["armory", "hermes-inventory"] });
      void queryClient.invalidateQueries({ queryKey: ["armory", "hermes-catalog"] });
      void queryClient.invalidateQueries({ queryKey: ["mcps"] });
    },
  });
  const rows = useMemo(
    () => mergeMcpCatalogue(inventory.data, showCatalog ? catalog.data : undefined),
    [inventory.data, showCatalog, catalog.data],
  );
  const needle = filter.trim().toLocaleLowerCase("en-US");
  const shown = rows.filter((row) => {
    const configured = Boolean(row.jarvis || row.hermes);
    if (scope === "configured" && !configured) return false;
    if (scope === "available" && (configured || !row.catalog || row.catalog.installed)) return false;
    return !needle || `${row.name} ${row.description}`.toLocaleLowerCase("en-US").includes(needle);
  });
  const configuredCount = rows.filter((row) => row.jarvis || row.hermes).length;
  const runningCount = (inventory.data?.jarvis ?? []).filter((server) => server.status === "running").length;
  const hermesEnabledCount = (inventory.data?.hermes ?? []).filter((server) => server.enabled).length;
  const configuredCountLabel =
    inventory.isLoading
      ? "…"
      : inventory.isError || !inventory.data || inventory.data.jarvisError || inventory.data.hermesError
        ? "—"
        : configuredCount;
  const runningCountLabel =
    inventory.isLoading
      ? "…"
      : inventory.isError || !inventory.data || inventory.data.jarvisError
        ? "—"
        : runningCount;
  const hermesEnabledCountLabel =
    inventory.isLoading
      ? "…"
      : inventory.isError || !inventory.data || inventory.data.hermesError
        ? "—"
        : hermesEnabledCount;
  const busyKey = action.isPending && action.variables ? `${action.variables.source}:${action.variables.name}` : null;

  function run(next: Action) {
    action.mutate(next);
  }

  const scopes = [
    ["all", "All"] as const,
    ["configured", "Configured"] as const,
    ["available", "Available to add"] as const,
  ];

  return (
    <section aria-label="MCP catalogue" data-testid="armory-mcp-catalogue" className="mt-6">
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-lg font-semibold text-slate-100">MCP catalogue</h2>
        <span className="text-xs text-slate-400">
          {configuredCountLabel} unique configured · {runningCountLabel} JARVIS running ·{" "}
          {hermesEnabledCountLabel} Hermes enabled
        </span>
      </div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder="Filter MCP servers"
          aria-label="Filter MCP catalogue"
          className="h-10 min-w-56 flex-1 rounded-lg border border-white/10 bg-white/[0.03] px-3 text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-cyan-300/50"
        />
        {scopes.map(([id, label]) => (
          <button
            key={id}
            type="button"
            aria-pressed={scope === id}
            onClick={() => setScope(id)}
            className={cn(
              "rounded-full border px-3 py-1.5 text-xs",
              scope === id ? "border-cyan-300/60 bg-cyan-400/15 text-cyan-100" : "border-white/10 text-slate-400",
            )}
          >
            {label}
          </button>
        ))}
        <button
          type="button"
          aria-pressed={showCatalog}
          onClick={() => {
            setShowCatalog((value) => !value);
            if (scope === "available") setScope("all");
          }}
          className="rounded-full border border-white/10 px-3 py-1.5 text-xs text-slate-300 hover:border-cyan-300/50"
        >
          {showCatalog ? "Hide Hermes catalog" : "Load available Hermes MCPs"}
        </button>
      </div>

      {inventory.isLoading && <p role="status" className="text-sm text-slate-400">Reading configured MCP servers…</p>}
      {inventory.isError && (
        <p role="alert" className="mb-2 text-xs text-amber-200">
          Could not read configured MCP servers.
        </p>
      )}
      {inventory.data?.jarvisError && <p role="alert" className="mb-2 text-xs text-amber-200">JARVIS list: {inventory.data.jarvisError}</p>}
      {inventory.data?.hermesError && <p role="alert" className="mb-2 text-xs text-amber-200">Hermes list: {inventory.data.hermesError}</p>}
      {catalog.isError && <p role="alert" className="mb-2 text-xs text-amber-200">Could not load Hermes&apos;s optional MCP catalog.</p>}
      {action.isError && <p role="alert" className="mb-2 text-xs text-rose-200">MCP action failed: {(action.error as Error).message}</p>}
      {actionNotice && <p role="status" className="mb-2 text-xs text-emerald-200">{actionNotice}</p>}

      <ul className="divide-y divide-white/5 rounded-xl border border-white/10 bg-white/[0.02] px-4">
        {shown.map((row) => {
          const jarvisKey = `jarvis:${row.jarvis?.name ?? ""}`;
          const hermesKey = `hermes:${row.hermes?.name ?? row.catalog?.name ?? ""}`;
          const isBusy = busyKey === jarvisKey || busyKey === hermesKey || busyKey === `catalog:${row.catalog?.name ?? ""}`;
          return (
            <li key={row.key} className="flex flex-wrap items-center gap-3 py-3">
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-slate-100">{row.name}</span>
                {row.description && <span className="block truncate text-xs text-slate-500">{row.description}</span>}
                <span className="mt-1 flex flex-wrap gap-2 text-[11px]">
                  {row.jarvis && (
                    <span className={row.jarvis.status === "running" ? "text-emerald-300" : "text-slate-400"}>
                      JARVIS · {row.jarvis.status === "running" ? "running" : row.jarvis.enabled ? "enabled, stopped" : "disabled"}
                      {row.jarvis.tools?.length
                        ? ` · ${row.jarvis.tools.length} ${row.jarvis.tools.length === 1 ? "tool" : "tools"}`
                        : ""}
                    </span>
                  )}
                  {row.hermes && <span className={row.hermes.enabled ? "text-emerald-300" : "text-slate-400"}>Hermes · {row.hermes.enabled ? "enabled" : "disabled"}</span>}
                  {!row.jarvis && !row.hermes && row.catalog && (
                    <span className={row.catalog.installed ? "text-slate-300" : "text-cyan-200"}>
                      {row.catalog.installed ? "Installed in Hermes" : "Available in Hermes catalog"}
                    </span>
                  )}
                  {row.jarvis?.error && <span className="text-rose-300">{row.jarvis.error}</span>}
                </span>
              </span>
              {row.jarvis && (
                <button
                  type="button"
                  disabled={action.isPending}
                  onClick={() => run({ source: "jarvis", name: row.jarvis!.name, enable: !row.jarvis!.enabled })}
                  className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-slate-200 disabled:opacity-50"
                >
                  {isBusy && busyKey === jarvisKey ? "Working…" : row.jarvis.enabled ? "Disable JARVIS" : "Enable JARVIS"}
                </button>
              )}
              {row.hermes && (
                <button
                  type="button"
                  disabled={action.isPending}
                  onClick={() => run({ source: "hermes", name: row.hermes!.name, enable: !row.hermes!.enabled })}
                  className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-slate-200 disabled:opacity-50"
                >
                  {isBusy && busyKey === hermesKey ? "Working…" : row.hermes.enabled ? "Disable Hermes" : "Enable Hermes"}
                </button>
              )}
              {!row.hermes && row.catalog && !row.catalog.installed && (
                <button
                  type="button"
                  disabled={action.isPending}
                  onClick={() => run({ source: "catalog", name: row.catalog!.name })}
                  className="rounded-lg border border-cyan-300/30 px-3 py-1.5 text-xs text-cyan-100 disabled:opacity-50"
                >
                  {isBusy ? "Adding…" : "Add to Hermes"}
                </button>
              )}
            </li>
          );
        })}
        {!inventory.isLoading && shown.length === 0 && (
          <li className="py-5 text-center text-sm text-slate-500">
            {showCatalog && catalog.isLoading ? "Loading available MCPs…" : "No MCP servers match this view."}
          </li>
        )}
      </ul>
    </section>
  );
}
