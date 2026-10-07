import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Tool armory pieces: the stat card, and the Hermes Agent group that shows what
// Hermes itself can use (discovered at runtime by GET /api/hermes/inventory,
// never a hard-coded count). Every part reports its own failure honestly.
// Each Hermes card opens its list; a row's button runs one Hermes command
// through POST /api/hermes/action (enable, disable, or install from the catalog).
// ---------------------------------------------------------------------------

export type HermesPart = {
  available: boolean;
  enabled: number;
  total: number;
  error: string | null;
};

export type HermesPlugin = { name: string; status: string; version?: string; source?: string; description?: string };
export type HermesToolset = { name: string; label: string; enabled: boolean; configured: boolean; tool_count: number };

export type HermesInventory = {
  available: boolean;
  reason: string | null;
  home: string | null;
  config_error: string | null;
  skills: {
    bundled: number;
    hub: number;
    local: number;
    external: number;
    plugin: number;
    total: number;
    truncated: boolean;
    items: { name: string; provenance: string; enabled?: boolean }[];
  };
  mcp_servers: { name: string; transport: string; enabled: boolean }[];
  mcp_status_checked: boolean;
  plugins: HermesPart & { items?: HermesPlugin[] };
  toolsets: HermesPart & { items?: HermesToolset[] };
  /** What Hermes ships and can add: its MCP catalog and optional skills. */
  catalog?: {
    available: boolean;
    mcp_servers: number;
    optional_skills: number;
    bundled_skills: number;
    error: string | null;
  };
};

export type HermesCatalog = {
  available: boolean;
  error: string | null;
  mcp_servers: { name: string; description: string; auth: string; installed: boolean }[];
  skills: { id: string; name: string; category: string; description: string; installed: boolean }[];
};

export function ArmoryStat({
  label,
  value,
  hint,
  onClick,
  active = false,
}: {
  label: string;
  value: string;
  hint: string;
  onClick?: () => void;
  active?: boolean;
}) {
  const body = (
    <>
      <span className="font-mono text-[10px] uppercase tracking-[0.25em] text-slate-500">{label}</span>
      <span className="mt-1 block text-2xl font-semibold text-slate-50">{value}</span>
      <span className="text-xs text-slate-400">{hint}</span>
    </>
  );
  const className = "block w-full rounded-xl border border-white/10 bg-white/[0.03] p-4 text-left";
  return onClick ? (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={active}
      className={cn(className, "hover:border-cyan-300/40", active && "border-cyan-300/60 bg-cyan-300/[0.06]")}
    >
      {body}
    </button>
  ) : (
    <div className={className}>{body}</div>
  );
}

export function skillBreakdown(skills: HermesInventory["skills"]): string {
  const parts: [string, number][] = [
    ["bundled", skills.bundled],
    ["hub", skills.hub],
    ["local", skills.local],
    ["external", skills.external],
    ["plugin", skills.plugin],
  ];
  return parts.map(([label, count]) => `${count} ${label}`).join(" · ");
}

type PanelId = "skills" | "mcp" | "plugins" | "toolsets" | "mcp_catalog" | "skill_catalog";

const PANEL_TITLES: Record<PanelId, string> = {
  skills: "Hermes skills",
  mcp: "Hermes MCP servers",
  plugins: "Hermes plugins",
  toolsets: "Hermes toolsets",
  mcp_catalog: "MCP servers Hermes can add",
  skill_catalog: "Optional skills Hermes can add",
};

type Row = {
  key: string;
  name: string;
  detail: string;
  on: boolean | null;
  /** The armory action this row's button sends, or null for a read-only row. */
  action: { kind: string; op: "enable" | "disable" | "install"; name: string } | null;
  label: string;
};

type ActionResult = { ok: boolean; kind: string; op: string; name: string; message: string; restart_needed: boolean };

function partStat(label: string, part: HermesPart, active: boolean, onClick: () => void) {
  if (!part.available) {
    return <ArmoryStat label={label} value="—" hint={part.error || "unavailable"} />;
  }
  return (
    <ArmoryStat
      label={label}
      value={`${part.enabled} / ${part.total}`}
      hint="enabled"
      active={active}
      onClick={onClick}
    />
  );
}

async function fetchInventory(refresh = false): Promise<HermesInventory> {
  const response = await fetch(refresh ? "/api/hermes/inventory?refresh=true" : "/api/hermes/inventory");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as HermesInventory;
}

async function fetchCatalog(): Promise<HermesCatalog> {
  const response = await fetch("/api/hermes/catalog");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as HermesCatalog;
}

async function postAction(action: NonNullable<Row["action"]>): Promise<ActionResult> {
  const response = await fetch("/api/hermes/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(action),
  });
  const body = (await response.json().catch(() => ({}))) as Partial<ActionResult> & { detail?: string };
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body as ActionResult;
}

function toggle(kind: string, name: string, on: boolean): Row["action"] {
  return { kind, op: on ? "disable" : "enable", name };
}

export function panelRows(panel: PanelId, data: HermesInventory, catalog: HermesCatalog | undefined): Row[] {
  switch (panel) {
    case "skills":
      return data.skills.items.map((skill) => {
        const on = skill.enabled !== false;
        return {
          key: skill.name,
          name: skill.name,
          detail: skill.provenance,
          on,
          action: toggle("skill", skill.name, on),
          label: on ? "Disable" : "Enable",
        };
      });
    case "mcp":
      return data.mcp_servers.map((server) => ({
        key: server.name,
        name: server.name,
        detail: server.transport,
        on: server.enabled,
        action: toggle("mcp", server.name, server.enabled),
        label: server.enabled ? "Disable" : "Enable",
      }));
    case "plugins":
      return (data.plugins.items ?? []).map((plugin) => {
        const on = plugin.status === "enabled";
        return {
          key: plugin.name,
          name: plugin.name,
          detail: [plugin.source, plugin.description].filter(Boolean).join(" · "),
          on,
          action: toggle("plugin", plugin.name, on),
          label: on ? "Disable" : "Enable",
        };
      });
    case "toolsets":
      return (data.toolsets.items ?? []).map((toolset) => ({
        key: toolset.name,
        name: toolset.label || toolset.name,
        detail: `${toolset.tool_count} tools${toolset.configured ? "" : " · needs setup in Hermes"}`,
        on: toolset.enabled,
        action: toggle("toolset", toolset.name, toolset.enabled),
        label: toolset.enabled ? "Disable" : "Enable",
      }));
    case "mcp_catalog":
      return (catalog?.mcp_servers ?? []).map((server) => ({
        key: server.name,
        name: server.name,
        detail: [server.description, server.auth === "oauth" ? "signs in on first use" : ""].filter(Boolean).join(" · "),
        on: server.installed ? true : null,
        action: server.installed ? null : { kind: "mcp_catalog", op: "install", name: server.name },
        label: server.installed ? "Installed" : "Install",
      }));
    case "skill_catalog":
      return (catalog?.skills ?? []).map((skill) => ({
        key: skill.id,
        name: skill.name,
        detail: [skill.category, skill.description].filter(Boolean).join(" · "),
        on: skill.installed ? true : null,
        action: skill.installed ? null : { kind: "skill_catalog", op: "install", name: skill.id },
        label: skill.installed ? "Installed" : "Install",
      }));
  }
}

function HermesPanel({ panel, data, onClose }: { panel: PanelId; data: HermesInventory; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const needsCatalog = panel === "mcp_catalog" || panel === "skill_catalog";
  const catalog = useQuery({
    queryKey: ["armory", "hermes-catalog"],
    queryFn: fetchCatalog,
    enabled: needsCatalog,
    staleTime: 30_000,
    retry: false,
  });

  const rows = panelRows(panel, data, catalog.data);
  const needle = filter.trim().toLowerCase();
  const shown = needle
    ? rows.filter((row) => `${row.name} ${row.detail}`.toLowerCase().includes(needle))
    : rows;

  async function run(row: Row) {
    if (!row.action) return;
    setBusy(row.key);
    setResult(null);
    try {
      const outcome = await postAction(row.action);
      const verb = { enable: "enabled", disable: "disabled", install: "installed" }[row.action.op];
      const head = outcome.ok ? `${row.name} ${verb}.` : `Hermes could not change ${row.name}.`;
      const tail = outcome.restart_needed ? " Restart Hermes to load it." : "";
      // Re-read before reporting, so the message and the row's new state appear together.
      await fetchInventory(true)
        .then((fresh) => queryClient.setQueryData(["armory", "hermes-inventory"], fresh))
        .catch(() => queryClient.invalidateQueries({ queryKey: ["armory", "hermes-inventory"] }));
      if (needsCatalog) await queryClient.invalidateQueries({ queryKey: ["armory", "hermes-catalog"] });
      setResult({ ok: outcome.ok, text: `${head}${tail}${outcome.message ? `\n${outcome.message}` : ""}` });
    } catch (error) {
      setResult({ ok: false, text: error instanceof Error ? error.message : String(error) });
    } finally {
      setBusy(null);
    }
  }

  let list;
  if (needsCatalog && catalog.isLoading) {
    list = (
      <p role="status" className="text-sm text-slate-400">
        Reading Hermes&apos;s catalog…
      </p>
    );
  } else if (needsCatalog && (catalog.isError || !catalog.data?.available)) {
    list = (
      <p role="alert" className="text-sm text-rose-200">
        {catalog.data?.error || "Could not read Hermes's catalog."}
      </p>
    );
  } else if (shown.length === 0) {
    list = <p className="text-sm text-slate-400">{rows.length ? "Nothing matches." : "Nothing here yet."}</p>;
  } else {
    list = (
      <ul className="max-h-96 divide-y divide-white/5 overflow-y-auto pr-1">
        {shown.map((row) => (
          <li key={row.key} className="flex items-center gap-3 py-2">
            <span
              aria-hidden
              className={cn(
                "h-2 w-2 shrink-0 rounded-full",
                row.on === true ? "bg-emerald-400" : row.on === false ? "bg-slate-600" : "bg-transparent",
              )}
            />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm text-slate-100">{row.name}</span>
              {row.detail && <span className="block truncate text-xs text-slate-500">{row.detail}</span>}
            </span>
            <button
              type="button"
              disabled={!row.action || busy !== null}
              onClick={() => void run(row)}
              aria-label={`${row.label} ${row.name}`}
              className={cn(
                "shrink-0 rounded-lg border px-3 py-1 text-xs",
                row.action
                  ? "border-cyan-300/30 text-cyan-100 hover:border-cyan-300/60 disabled:opacity-50"
                  : "border-white/10 text-slate-500",
              )}
            >
              {busy === row.key ? "Working…" : row.label}
            </button>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div
      role="region"
      aria-label={PANEL_TITLES[panel]}
      data-testid="armory-hermes-panel"
      className="mt-3 rounded-xl border border-cyan-300/20 bg-white/[0.02] p-4"
    >
      <div className="mb-3 flex items-center gap-3">
        <p className="flex-1 text-sm font-medium text-slate-100">
          {PANEL_TITLES[panel]} <span className="text-slate-500">({rows.length})</span>
        </p>
        <button type="button" onClick={onClose} className="text-xs text-slate-400 hover:text-slate-100">
          Close
        </button>
      </div>
      <input
        type="search"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
        placeholder="Filter"
        aria-label={`Filter ${PANEL_TITLES[panel]}`}
        className="mb-2 h-9 w-full rounded-lg border border-white/10 bg-transparent px-3 text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-cyan-300/50"
      />
      {result && (
        <p
          role="status"
          className={cn("mb-2 whitespace-pre-line text-xs", result.ok ? "text-emerald-200" : "text-rose-200")}
        >
          {result.text}
        </p>
      )}
      {list}
      {panel === "skills" && data.skills.truncated && (
        <p className="mt-2 text-xs text-slate-500">Showing the first {data.skills.items.length} skills.</p>
      )}
    </div>
  );
}

export function HermesArmoryGroup() {
  const [panel, setPanel] = useState<PanelId | null>(null);
  const { data, isLoading, isError } = useQuery({
    queryKey: ["armory", "hermes-inventory"],
    queryFn: () => fetchInventory(),
    staleTime: 30_000,
    retry: false,
  });
  const open = (id: PanelId) => () => setPanel((current) => (current === id ? null : id));
  let body;
  if (isLoading) {
    body = (
      <p role="status" className="text-sm text-slate-400">
        Checking Hermes Agent…
      </p>
    );
  } else if (isError || !data) {
    body = (
      <p role="alert" className="text-sm text-rose-200">
        Could not read the Hermes Agent inventory.
      </p>
    );
  } else if (!data.available) {
    body = (
      <p role="status" className="text-sm text-slate-400">
        {data.reason || "Hermes not found on this computer"}
      </p>
    );
  } else {
    const enabledMcp = data.mcp_servers.filter((server) => server.enabled).length;
    body = (
      <>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <ArmoryStat
            label="Skills"
            value={String(data.skills.total)}
            hint={skillBreakdown(data.skills)}
            active={panel === "skills"}
            onClick={open("skills")}
          />
          <ArmoryStat
            label="MCP servers"
            value={`${enabledMcp} / ${data.mcp_servers.length}`}
            hint={
              data.mcp_status_checked ? "enabled / configured" : "enabled / configured · connection not checked"
            }
            active={panel === "mcp"}
            onClick={open("mcp")}
          />
          {partStat("Plugins", data.plugins, panel === "plugins", open("plugins"))}
          {partStat("Toolsets", data.toolsets, panel === "toolsets", open("toolsets"))}
        </div>
        {data.catalog && (
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {data.catalog.available ? (
              <>
                <ArmoryStat
                  label="MCP catalog"
                  value={String(data.catalog.mcp_servers)}
                  hint="servers Hermes can add (hermes mcp catalog)"
                  active={panel === "mcp_catalog"}
                  onClick={open("mcp_catalog")}
                />
                <ArmoryStat
                  label="Skill catalog"
                  value={String(data.catalog.bundled_skills + data.catalog.optional_skills)}
                  hint={`${data.catalog.bundled_skills} built in · ${data.catalog.optional_skills} optional`}
                  active={panel === "skill_catalog"}
                  onClick={open("skill_catalog")}
                />
              </>
            ) : (
              <ArmoryStat label="Catalog" value="—" hint={data.catalog.error || "unavailable"} />
            )}
          </div>
        )}
        {panel && <HermesPanel key={panel} panel={panel} data={data} onClose={() => setPanel(null)} />}
        {data.config_error && <p className="mt-2 text-xs text-amber-200/90">{data.config_error}</p>}
      </>
    );
  }
  return (
    <section aria-label="Hermes Agent" data-testid="armory-hermes" className="mt-6">
      <p className="mb-3 font-mono text-[11px] uppercase tracking-[0.3em] text-slate-500">
        Hermes Agent <span className="normal-case tracking-normal text-slate-600">discovered at runtime</span>
      </p>
      {body}
    </section>
  );
}
