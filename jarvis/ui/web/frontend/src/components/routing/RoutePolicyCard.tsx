import { useEffect, useMemo, useState } from "react";
import { Loader2, RotateCcw, Route } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { BrandedSelect, type BrandedSelectOption } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { ProviderDescriptor } from "@/hooks/useProviders";
import { fill, useT } from "@/i18n";
import {
  fetchRoutePolicy,
  restoreRoutePolicy,
  saveRoutePolicy,
  type RoutePolicy,
  type RouteSelection,
  type RouteTarget,
} from "@/lib/routePolicyApi";
import { useEventStore } from "@/store/events";

/**
 * Fast / deep / escalation routing on the Brain tab (`[brain.route_policy]`).
 *
 * The backend policy existed with no in-app surface, so turning it on meant a
 * hand edit of jarvis.toml. Here a person picks the provider and model for
 * quick turns and for hard ones (a local OpenAI-compatible server such as
 * Qwen is just another Brain provider, marked "runs on this computer"), keeps
 * escalation to a delegate behind phrases they must say, and sees what the
 * last turn actually ran on: tier, reason, model, skipped targets, outcome —
 * straight from `BrainRouteSelected`. Saving applies from the next turn and
 * survives a restart; "Undo last change" restores the table as it was.
 */
export function RoutePolicyCard({ providers }: { providers: ProviderDescriptor[] }) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [saved, setSaved] = useState<RoutePolicy | null>(null);
  const [draft, setDraft] = useState<RoutePolicy | null>(null);
  const [canRestore, setCanRestore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    let alive = true;
    fetchRoutePolicy()
      .then((view) => {
        if (!alive) return;
        setSaved(view.policy);
        setDraft(view.policy);
        setCanRestore(view.can_restore);
      })
      .catch((err: unknown) => {
        if (alive) setLoadError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      alive = false;
    };
  }, []);

  const brains = useMemo(() => providers.filter((p) => p.tier === "brain"), [providers]);
  const options = useMemo<BrandedSelectOption[]>(
    () => [
      { value: "", label: t("route_policy.target_unset") },
      ...brains.map((p) => ({
        value: p.id,
        label: p.label,
        hint: p.billing === "local" ? t("route_policy.on_device") : p.configured ? undefined : t("route_policy.needs_setup"),
      })),
    ],
    [brains, t],
  );
  const lastRoute = useLastRoute();

  if (loadError) {
    return (
      <section data-testid="route-policy" className="rounded-surface border border-border bg-card p-4 text-xs text-muted-foreground">
        {fill(t("route_policy.load_failed"), { detail: loadError })}
      </section>
    );
  }
  if (!draft || !saved) {
    return (
      <section data-testid="route-policy" className="rounded-surface border border-border bg-card p-4 text-xs text-muted-foreground">
        <Loader2 className="mr-2 inline h-3.5 w-3.5 animate-spin" aria-hidden />
        {t("route_policy.loading")}
      </section>
    );
  }

  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const setTarget = (tier: "fast" | "deep", patch: Partial<RouteTarget>) =>
    setDraft({ ...draft, [tier]: { ...draft[tier], ...patch } });
  const pickProvider = (tier: "fast" | "deep", id: string) => {
    const descriptor = brains.find((p) => p.id === id);
    // A local server is the honest default for "runs on this computer"; a
    // loopback gateway to a cloud model stays the person's call to untick.
    setTarget(tier, { provider: id, model: "", local: descriptor?.billing === "local" });
  };
  const phrases = draft.escalation.trigger_phrases.join(", ");

  const run = async (action: () => Promise<{ policy: RoutePolicy; can_restore: boolean }>, ok: string) => {
    setBusy(true);
    try {
      const view = await action();
      setSaved(view.policy);
      setDraft(view.policy);
      setCanRestore(view.can_restore);
      pushToast("success", ok);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const save = () =>
    run(
      () =>
        saveRoutePolicy({
          enabled: draft.enabled,
          fast: cleanTarget(draft.fast),
          deep: cleanTarget(draft.deep),
          escalation: {
            enabled: draft.escalation.enabled,
            agent: draft.escalation.agent.trim(),
            trigger_phrases: draft.escalation.trigger_phrases.map((p) => p.trim()).filter(Boolean),
          },
          allow_cloud_vision: draft.allow_cloud_vision,
        }),
      t("route_policy.saved"),
    );

  return (
    <section
      data-testid="route-policy"
      aria-labelledby="route-policy-title"
      className="mt-6 space-y-4 rounded-surface border border-border bg-card p-4"
    >
      <header className="flex flex-wrap items-start gap-3">
        <Route className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <div className="min-w-0 flex-1 space-y-1">
          <h3 id="route-policy-title" className="text-sm font-medium text-foreground">
            {t("route_policy.title")}
          </h3>
          <p className="text-xs leading-relaxed text-muted-foreground">{t("route_policy.desc")}</p>
        </div>
        <label className="flex items-center gap-2 text-xs font-medium text-foreground">
          <Switch
            checked={draft.enabled}
            onCheckedChange={(enabled) => setDraft({ ...draft, enabled })}
            aria-label={t("route_policy.enabled")}
            data-testid="route-policy-enabled"
          />
          {t("route_policy.enabled")}
        </label>
      </header>

      <div className="grid gap-3 md:grid-cols-2">
        {(["fast", "deep"] as const).map((tier) => (
          <TargetEditor
            key={tier}
            tier={tier}
            target={draft[tier]}
            options={options}
            onProvider={(id) => pickProvider(tier, id)}
            onChange={(patch) => setTarget(tier, patch)}
          />
        ))}
      </div>

      <div className="space-y-2 rounded-control border border-border p-3">
        <label className="flex items-center gap-2 text-xs font-medium text-foreground">
          <Switch
            checked={draft.escalation.enabled}
            onCheckedChange={(enabled) =>
              setDraft({ ...draft, escalation: { ...draft.escalation, enabled } })
            }
            aria-label={t("route_policy.escalation")}
            data-testid="route-policy-escalation"
          />
          {t("route_policy.escalation")}
        </label>
        <p className="text-xs leading-relaxed text-muted-foreground">{t("route_policy.escalation_desc")}</p>
        {draft.escalation.enabled ? (
          <div className="grid gap-2 md:grid-cols-2">
            <Input
              value={draft.escalation.agent}
              onChange={(e) => setDraft({ ...draft, escalation: { ...draft.escalation, agent: e.target.value } })}
              placeholder={t("route_policy.agent_placeholder")}
              aria-label={t("route_policy.agent")}
              data-testid="route-policy-agent"
            />
            <Input
              value={phrases}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  escalation: { ...draft.escalation, trigger_phrases: e.target.value.split(",") },
                })
              }
              placeholder={t("route_policy.phrases_placeholder")}
              aria-label={t("route_policy.phrases")}
              data-testid="route-policy-phrases"
            />
          </div>
        ) : null}
      </div>

      <label className="flex items-start gap-2 text-xs text-foreground">
        <Switch
          checked={draft.allow_cloud_vision}
          onCheckedChange={(allow_cloud_vision) => setDraft({ ...draft, allow_cloud_vision })}
          aria-label={t("route_policy.cloud_vision")}
          data-testid="route-policy-cloud-vision"
        />
        <span className="space-y-0.5">
          <span className="block font-medium">{t("route_policy.cloud_vision")}</span>
          <span className="block text-muted-foreground">{t("route_policy.cloud_vision_desc")}</span>
        </span>
      </label>

      <LastRoute selection={lastRoute} />

      <footer className="flex flex-wrap items-center justify-end gap-2">
        {canRestore ? (
          <Button
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={() => void run(restoreRoutePolicy, t("route_policy.restored"))}
            data-testid="route-policy-restore"
          >
            <RotateCcw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            {t("route_policy.restore")}
          </Button>
        ) : null}
        <Button size="sm" disabled={!dirty || busy} onClick={() => void save()} data-testid="route-policy-save">
          {busy ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
          {t("route_policy.save")}
        </Button>
      </footer>
    </section>
  );
}

function cleanTarget(target: RouteTarget): RouteTarget {
  const model = (target.model ?? "").trim();
  return { provider: target.provider, model: model || null, local: target.local };
}

function TargetEditor({
  tier,
  target,
  options,
  onProvider,
  onChange,
}: {
  tier: "fast" | "deep";
  target: RouteTarget;
  options: BrandedSelectOption[];
  onProvider: (id: string) => void;
  onChange: (patch: Partial<RouteTarget>) => void;
}) {
  const t = useT();
  return (
    <div className="space-y-2 rounded-control border border-border p-3" data-testid={`route-policy-${tier}`}>
      <div className="space-y-0.5">
        <p className="text-xs font-medium text-foreground">{t(`route_policy.${tier}`)}</p>
        <p className="text-xs text-muted-foreground">{t(`route_policy.${tier}_desc`)}</p>
      </div>
      <BrandedSelect
        value={target.provider}
        options={options}
        onValueChange={onProvider}
        ariaLabel={t(`route_policy.${tier}`)}
        testId={`route-policy-${tier}-provider`}
      />
      <Input
        value={target.model ?? ""}
        onChange={(e) => onChange({ model: e.target.value })}
        placeholder={t("route_policy.model_placeholder")}
        aria-label={fill(t("route_policy.model_label"), { tier: t(`route_policy.${tier}`) })}
        disabled={!target.provider}
        data-testid={`route-policy-${tier}-model`}
      />
      <label className="flex items-center gap-2 text-xs text-muted-foreground">
        <Switch
          checked={target.local}
          onCheckedChange={(local) => onChange({ local })}
          disabled={!target.provider}
          aria-label={t("route_policy.local")}
          data-testid={`route-policy-${tier}-local`}
        />
        {t("route_policy.local")}
      </label>
    </div>
  );
}

/** The newest `BrainRouteSelected` on the event log, if any turn was routed. */
function useLastRoute(): RouteSelection | null {
  const events = useEventStore((s) => s.events);
  return useMemo(() => {
    const hit = events.find((e) => e.name === "BrainRouteSelected");
    return hit ? (hit.payload as RouteSelection) : null;
  }, [events]);
}

function LastRoute({ selection }: { selection: RouteSelection | null }) {
  const t = useT();
  if (!selection) {
    return (
      <p className="text-xs text-muted-foreground" data-testid="route-policy-last">
        {t("route_policy.last_none")}
      </p>
    );
  }
  const ran = selection.chain[0] ?? "";
  return (
    <dl
      className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-control bg-secondary/50 p-3 text-xs"
      data-testid="route-policy-last"
    >
      <dt className="text-muted-foreground">{t("route_policy.last_tier")}</dt>
      <dd className="text-foreground">{selection.tier || "—"}</dd>
      <dt className="text-muted-foreground">{t("route_policy.last_model")}</dt>
      <dd className="break-all font-mono text-foreground">{ran || "—"}</dd>
      <dt className="text-muted-foreground">{t("route_policy.last_reason")}</dt>
      <dd className="text-foreground">{selection.reason || "—"}</dd>
      {selection.chain.length > 1 ? (
        <>
          <dt className="text-muted-foreground">{t("route_policy.last_fallback")}</dt>
          <dd className="break-all font-mono text-foreground">{selection.chain.slice(1).join(" → ")}</dd>
        </>
      ) : null}
      {selection.excluded.length ? (
        <>
          <dt className="text-muted-foreground">{t("route_policy.last_excluded")}</dt>
          <dd className="break-all font-mono text-foreground">{selection.excluded.join(", ")}</dd>
        </>
      ) : null}
      {selection.outcome ? (
        <>
          <dt className="text-muted-foreground">{t("route_policy.last_outcome")}</dt>
          <dd className="text-foreground">{selection.outcome}</dd>
        </>
      ) : null}
    </dl>
  );
}
