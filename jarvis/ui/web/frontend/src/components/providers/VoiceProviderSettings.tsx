import { Waypoints } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ProviderDescriptor, ProviderTier, SectionHealth } from "@/hooks/useProviders";
import { useT } from "@/i18n";
import { RealtimeTab } from "@/views/apikeys/RealtimeTab";

/** Keep the public voice-mode shell while sharing the realtime provider setup. */
export function VoiceProviderSettings({
  providers, loading, error, onChanged, onActivateOptimistic, health,
  localMode, onDisableLocalMode, onUsePipeline,
}: {
  providers: ProviderDescriptor[];
  loading: boolean;
  error: string | null;
  onChanged: () => void;
  onActivateOptimistic: (tier: ProviderTier, id: string) => void;
  health?: SectionHealth;
  localMode: boolean;
  onDisableLocalMode: () => void;
  /**
   * Moves voice to Pipeline and opens its brain picker. Hermes, Nous Portal
   * and a local Qwen server have no native live-audio model, so they never
   * appear in this list; without this line the person was left looking for
   * them here. There is deliberately no stand-in "Hermes" realtime row.
   */
  onUsePipeline?: () => void;
}) {
  const t = useT();
  const visible = providers.filter(
    (provider) => provider.tier === "realtime" && (!localMode || provider.billing === "local"),
  );
  return (
    <section role="tabpanel" aria-label={t("live.setup_title")} className="space-y-5">
      {onUsePipeline ? (
        <div
          data-testid="realtime-pipeline-hint"
          className="flex flex-wrap items-start gap-3 rounded-surface border border-border bg-card px-4 py-3"
        >
          <Waypoints className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          <div className="min-w-0 flex-1 space-y-1">
            <p className="text-sm font-medium text-foreground">{t("apikeys_view.pipeline_hint_title")}</p>
            <p className="text-xs leading-relaxed text-muted-foreground">{t("apikeys_view.pipeline_hint_body")}</p>
          </div>
          <Button variant="outline" size="sm" onClick={onUsePipeline} data-testid="realtime-pipeline-hint-use">
            {t("apikeys_view.pipeline_hint_action")}
          </Button>
        </div>
      ) : null}
      <RealtimeTab providers={visible} loading={loading} error={error}
        onChanged={onChanged} onActivateOptimistic={onActivateOptimistic} health={health} />
      {localMode && !visible.length ? (
        <Button variant="outline" onClick={onDisableLocalMode}>
          {t("live.show_cloud")}
        </Button>
      ) : null}
      <p className="text-xs leading-relaxed text-muted-foreground">
        {t("live.agents_separate")}
      </p>
    </section>
  );
}
