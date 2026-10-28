/**
 * Local-only mode (owner, 2026-10-25: "replace every paid option with open
 * source local"). When the route policy keeps every turn on this machine
 * (`[brain.route_policy]` enabled with a local fast tier, which
 * `jarvis system local` sets), the app offers only what runs here: the
 * provider console and the chat's model picker leave out cloud and paid
 * providers, and anything the policy denies (Hermes among them).
 *
 * Outside that mode only the deny list applies. A policy that cannot be read
 * hides nothing: the backend's own policy still refuses a denied provider.
 */
import { fetchRoutePolicy } from "@/lib/routePolicyApi";

export interface LocalScope {
  localOnly: boolean;
  deny: ReadonlySet<string>;
}

export const OPEN_SCOPE: LocalScope = { localOnly: false, deny: new Set() };

/** The chat rows that run on this machine (`jarvis/agent_chat/catalog.py`). */
export const LOCAL_CHAT_ROWS: ReadonlySet<string> = new Set(["local-openai", "ollama"]);

export async function readLocalScope(): Promise<LocalScope> {
  try {
    const { policy } = await fetchRoutePolicy();
    if (!policy || typeof policy !== "object") return OPEN_SCOPE;
    if (!policy.enabled) return OPEN_SCOPE;
    return {
      localOnly: Boolean(policy.fast?.local),
      deny: new Set(Array.isArray(policy.deny_providers) ? policy.deny_providers : []),
    };
  } catch (error) {
    console.info("Route policy unreadable; showing every provider:", error);
    return OPEN_SCOPE;
  }
}

/** Whether a provider card is offered; `billing` "local" means it runs here. */
export function providerOffered(scope: LocalScope, id: string, billing: unknown): boolean {
  if (scope.deny.has(id)) return false;
  return !scope.localOnly || billing === "local";
}

/** Whether a chat row is offered in the model picker. */
export function chatRowOffered(scope: LocalScope, id: string): boolean {
  if (scope.deny.has(id)) return false;
  return !scope.localOnly || LOCAL_CHAT_ROWS.has(id);
}
