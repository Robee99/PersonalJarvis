import { Brain, Mic, Volume2 } from "lucide-react";
import { BrainModelSelector } from "@/components/BrainModelSelector";
import { saveBrainProviderModel, type ProviderDescriptor } from "@/hooks/useProviders";
import { useT } from "@/i18n";

/** One model choice activates Hermes for both Jarvis chat and Pipeline voice. */
export function AssistantBrainPanel({ providers, onChanged, onVoice }: {
  providers: ProviderDescriptor[];
  onChanged: () => void;
  onVoice: (tier: "stt" | "tts") => void;
}) {
  const t = useT();
  const activeBrain = providers.find((p) => p.tier === "brain" && p.active);
  return (
    <section data-testid="assistant-brain-panel" className="space-y-5 rounded-2xl border border-border bg-card p-5">
      <header className="flex items-start gap-3">
        <Brain className="mt-1 h-5 w-5 text-primary" aria-hidden />
        <div className="min-w-0 space-y-1">
          <h2 className="text-lg font-semibold text-foreground">{t("assistant_brain.title")}</h2>
          <p className="text-sm text-muted-foreground">{t("assistant_brain.description")}</p>
        </div>
      </header>
      {activeBrain?.id !== "hermes" && (
        <p role="status" className="rounded-lg bg-secondary px-3 py-2 text-sm text-foreground">
          {t("assistant_brain.activate_note").replace("{provider}", activeBrain?.label ?? t("assistant_brain.unset"))}
        </p>
      )}
      <BrainModelSelector
        providerId="hermes"
        headingLabel={t("assistant_brain.model")}
        healthSection="brain"
        healthActive
        onSave={async (model) => {
          const result = await saveBrainProviderModel("hermes", model, true, true);
          onChanged();
          return result;
        }}
      />
      <p className="text-xs leading-relaxed text-muted-foreground">{t("assistant_brain.pin_note")}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {(["stt", "tts"] as const).map((tier) => {
          const current = providers.find((p) => p.tier === tier && p.active);
          const Icon = tier === "stt" ? Mic : Volume2;
          return (
            <button key={tier} type="button" onClick={() => onVoice(tier)} className="flex items-center gap-3 rounded-xl border border-border bg-background px-4 py-3 text-left hover:bg-secondary">
              <Icon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
              <span className="min-w-0">
                <span className="block text-xs text-muted-foreground">{t(`assistant_brain.${tier}`)}</span>
                <span className="block truncate text-sm font-medium text-foreground">{current?.label ?? t("assistant_brain.unset")}</span>
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
