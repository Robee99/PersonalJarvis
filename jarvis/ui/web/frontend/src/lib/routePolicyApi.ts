/**
 * `[brain.route_policy]` over `/api/brain/route-policy` — the fast/deep/
 * escalation routing the Brain tab edits. Mirrors `BrainRoutePolicyConfig`
 * (jarvis/core/config.py) minus the test-only tier pin.
 */

export interface RouteTarget {
  provider: string;
  /** Absent / null = the provider's own model. */
  model?: string | null;
  /** Runs on this computer, so images may go to it. */
  local: boolean;
}

export interface RouteEscalation {
  enabled: boolean;
  via: string;
  agent: string;
  trigger_phrases: string[];
  deadline_s: number;
  poll_interval_s: number;
  max_per_session: number;
  max_context_chars: number;
}

export interface RoutePolicy {
  enabled: boolean;
  fast: RouteTarget;
  deep: RouteTarget;
  escalation: RouteEscalation;
  deny_providers: string[];
  deny_model_prefixes: string[];
  allow_cloud_vision: boolean;
}

export interface RoutePolicyView {
  policy: RoutePolicy;
  can_restore: boolean;
}

export type RoutePolicyPatch = Partial<Omit<RoutePolicy, "escalation">> & {
  escalation?: Partial<RouteEscalation>;
};

async function parse(res: Response): Promise<RoutePolicyView> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return body as RoutePolicyView;
}

export async function fetchRoutePolicy(): Promise<RoutePolicyView> {
  return parse(await fetch("/api/brain/route-policy"));
}

export async function saveRoutePolicy(patch: RoutePolicyPatch): Promise<RoutePolicyView> {
  return parse(
    await fetch("/api/brain/route-policy", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  );
}

export async function restoreRoutePolicy(): Promise<RoutePolicyView> {
  return parse(await fetch("/api/brain/route-policy/restore", { method: "POST" }));
}

/** The `BrainRouteSelected` event payload (jarvis/core/events.py). */
export interface RouteSelection {
  tier: string;
  reason: string;
  intent_level: string;
  /** `provider:model` per attempt, in order. */
  chain: string[];
  /** `tier:provider:why` per skipped target. */
  excluded: string[];
  outcome: string;
  elapsed_ms: number;
}
