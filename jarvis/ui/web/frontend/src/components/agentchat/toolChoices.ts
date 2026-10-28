import { isPluginOwnedSkill } from "@/lib/pluginFamilies";

/** Mirrors the composer catalog's Pydantic model; selections send IDs only. */
export type ToolCategory =
  "plugins" | "skills" | "mcp" | "memory" | "web" | "files" | "automation" | "system" | "cli";
export type ToolBrowseScope = "computer" | "all";
export const TOOL_CATEGORIES: ToolCategory[] = [
  "plugins",
  "skills",
  "mcp",
  "memory",
  "web",
  "files",
  "automation",
  "system",
  "cli",
];

const PC_TOOL_NAMES = new Set([
  "pc_pilot",
  "computer_use",
  "app_control",
  "app_ui_click",
  "app_ui_type",
  "app_ui_snapshot",
  "app_ui_read",
  "my_chrome",
  "society_browser",
  "browser_navigate",
  "show_in_gods_eye_view",
  "world_globe",
]);

const PC_MCP_SERVERS = new Set([
  "filesystem",
  "windows",
  "browser-harness",
  "mcp-browser-harness",
  "browser-use",
  "agent-browser",
  "playwright",
  "windows-desktop",
  "cua-driver",
  "desktop-commander",
  "computer-use",
]);

const PC_SKILLS = new Set([
  "browser-skill",
  "browserskill",
  "tencent-browser",
  "tencent-browser-skill",
  "tencent-browserskill",
  "bsk",
  "my-chrome",
  "pc-pilot",
  "computer-use",
  "browser-use",
]);

function isPcMcp(row: ToolChoice): boolean {
  const raw = row.id.startsWith("mcp:") ? row.id.slice(4) : row.group || row.label;
  const server = raw.trim().toLowerCase().replace(/_/g, "-").replace(/-mcp$/, "");
  return PC_MCP_SERVERS.has(server);
}

function isPcSkill(row: ToolChoice): boolean {
  const slug = (row.skill || row.id.replace(/^skill:/, "") || row.label)
    .trim()
    .toLowerCase()
    .replace(/_/g, "-");
  return PC_SKILLS.has(slug);
}

function isPcTool(row: ToolChoice): boolean {
  return row.tool_names.some((name) => {
    const clean = name.trim().toLowerCase();
    const leaf = clean.slice(clean.lastIndexOf("/") + 1);
    return (
      PC_TOOL_NAMES.has(leaf) ||
      leaf === "get_browser_state"
    );
  });
}

export interface ToolChoice {
  id: string;
  label: string;
  description: string;
  category: ToolCategory;
  group: string;
  brand: string;
  available: boolean;
  tool_names: string[];
  skill: string;
  /** A plugin the owner added (custom connector or upload). */
  user_added?: boolean;
}

export interface ToolSearchResult {
  items: ToolChoice[];
  mode: "browse" | "semantic" | "text";
  total: number;
}

export async function searchTools(
  params: Record<string, string>,
  signal: AbortSignal,
): Promise<ToolSearchResult> {
  const res = await fetch(`/api/agent-chat/tools?${new URLSearchParams(params)}`, { signal });
  if (!res.ok) throw new Error(`tool-search:${res.status}`);
  return res.json();
}

/**
 * The Add menu lists connectors, not the skills and commands behind them.
 * Disconnected plugins stay hidden until someone searches for them — except
 * the ones the owner added, which show with a Connect action.
 */
export function browseToolRows(
  items: ToolChoice[],
  query = "",
  scope: ToolBrowseScope = "all",
): ToolChoice[] {
  const searching = Boolean(query.trim());
  return items.filter((row) => {
    if (row.category === "plugins" && row.id.startsWith("tool:")) return false;
    if (row.category === "plugins" && !row.available && !searching && !row.user_added) return false;
    if (scope === "all") {
      if (row.category === "skills" && isPluginOwnedSkill(row.skill || row.label || row.id))
        return false;
      return true;
    }
    if (row.category === "plugins") return false;
    if (row.category === "mcp") {
      return isPcMcp(row) || isPcTool(row);
    }
    if (row.category === "skills") {
      return isPcSkill(row) && !isPluginOwnedSkill(row.skill || row.label || row.id);
    }
    if (row.category === "web" || row.category === "system") return isPcTool(row);
    return false;
  });
}

/** Old events have no selections. Discard malformed receipts. */
export function readToolChoices(value: unknown): ToolChoice[] {
  if (!Array.isArray(value)) return [];
  return value.filter((row): row is ToolChoice =>
    Boolean(
      row &&
      typeof row.id === "string" &&
      typeof row.label === "string" &&
      TOOL_CATEGORIES.includes(row.category) &&
      typeof row.brand === "string",
    ),
  );
}
