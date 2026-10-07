import { useQuery } from "@tanstack/react-query";

import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Tool armory pieces: the stat card, and the Hermes Agent group that shows what
// Hermes itself can use (discovered at runtime by GET /api/hermes/inventory,
// never a hard-coded count). Every part reports its own failure honestly.
// ---------------------------------------------------------------------------

export type HermesPart = {
  available: boolean;
  enabled: number;
  total: number;
  error: string | null;
};

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
    items: { name: string; provenance: string }[];
  };
  mcp_servers: { name: string; transport: string; enabled: boolean }[];
  mcp_status_checked: boolean;
  plugins: HermesPart;
  toolsets: HermesPart;
  /** What Hermes ships and can add: its MCP catalog and optional skills. */
  catalog?: {
    available: boolean;
    mcp_servers: number;
    optional_skills: number;
    bundled_skills: number;
    error: string | null;
  };
};

export function ArmoryStat({
  label,
  value,
  hint,
  onClick,
}: {
  label: string;
  value: string;
  hint: string;
  onClick?: () => void;
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
    <button type="button" onClick={onClick} className={cn(className, "hover:border-cyan-300/40")}>
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

function partStat(label: string, part: HermesPart) {
  if (!part.available) {
    return <ArmoryStat label={label} value="—" hint={part.error || "unavailable"} />;
  }
  return <ArmoryStat label={label} value={`${part.enabled} / ${part.total}`} hint="enabled" />;
}

async function fetchInventory(): Promise<HermesInventory> {
  const response = await fetch("/api/hermes/inventory");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as HermesInventory;
}

export function HermesArmoryGroup() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["armory", "hermes-inventory"],
    queryFn: fetchInventory,
    staleTime: 30_000,
    retry: false,
  });
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
          <ArmoryStat label="Skills" value={String(data.skills.total)} hint={skillBreakdown(data.skills)} />
          <ArmoryStat
            label="MCP servers"
            value={`${enabledMcp} / ${data.mcp_servers.length}`}
            hint={
              data.mcp_status_checked ? "enabled / configured" : "enabled / configured · connection not checked"
            }
          />
          {partStat("Plugins", data.plugins)}
          {partStat("Toolsets", data.toolsets)}
        </div>
        {data.catalog && (
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {data.catalog.available ? (
              <>
                <ArmoryStat
                  label="MCP catalog"
                  value={String(data.catalog.mcp_servers)}
                  hint="servers Hermes can add (hermes mcp catalog)"
                />
                <ArmoryStat
                  label="Skill catalog"
                  value={String(data.catalog.bundled_skills + data.catalog.optional_skills)}
                  hint={`${data.catalog.bundled_skills} built in · ${data.catalog.optional_skills} optional`}
                />
              </>
            ) : (
              <ArmoryStat label="Catalog" value="—" hint={data.catalog.error || "unavailable"} />
            )}
          </div>
        )}
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
