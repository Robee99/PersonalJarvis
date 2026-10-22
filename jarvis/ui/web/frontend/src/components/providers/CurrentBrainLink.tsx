import { ArrowRight, Brain } from "lucide-react";

import { isHermesBrain } from "@/hooks/useHermesBrain";
import { useProviders } from "@/hooks/useProviders";
import { useT } from "@/i18n";
import { openSettingsDestination } from "@/lib/openSettings";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

/**
 * Who answers right now, read-only, with the way to change it.
 *
 * The brain (who answers, and with which model) is chosen in exactly one
 * place: the brain picker on Settings › Model. Every other surface that used
 * to carry its own switch (the debug view, the API Keys page's Agents tab,
 * the voice notice in a chat, the local-models setup) shows this line
 * instead, so the person sees the current brain and reaches the one picker in
 * one click. It never calls `POST /api/brain/switch` itself.
 */

/** The model ids that mean "Hermes picks the model per turn". */
const HERMES_AUTO = new Set(["", "hermes-agent"]);

/**
 * The model part of a stored model id, as a person reads it:
 * `local-qwen::qwen` → `qwen`, `http://127.0.0.1:11440|qwen3-vl` → `qwen3-vl`.
 */
export function readableModel(model: string): string {
  const bare = model.includes("::") ? model.split("::").slice(1).join("::") : model;
  return bare.replace(/^https?:\/\/[^|]*\|/, "");
}

/** "Hermes Agent · qwen3-vl", "Hermes Agent · Hermes decides", "Claude · claude-opus". */
export function describeBrain(
  provider: string,
  model: string,
  labelFor: (id: string) => string,
  t: (key: string) => string,
): { who: string; model: string } {
  if (!provider || provider === "unknown") return { who: "", model: "" };
  if (isHermesBrain(provider)) {
    return {
      who: t("brain_picker.hermes_title"),
      model: HERMES_AUTO.has(model) ? t("brain_picker.auto_title") : readableModel(model),
    };
  }
  return { who: labelFor(provider), model: readableModel(model) };
}

export function CurrentBrainLink({ className }: { className?: string }) {
  const t = useT();
  const provider = useEventStore((s) => s.brainProvider);
  const model = useEventStore((s) => s.brainModel);
  const { providers } = useProviders();
  const labelFor = (id: string) => providers.find((p) => p.id === id)?.label ?? id;
  const brain = describeBrain(provider, model, labelFor, t);

  return (
    <div data-testid="current-brain" className={cn("space-y-2", className)}>
      <p className="flex items-start gap-2 text-sm text-foreground">
        <Brain className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <span className="min-w-0">
          <span className="block text-meta text-muted-foreground">{t("brain_picker.current_label")}</span>
          <span data-testid="current-brain-value" className="block font-medium">
            {brain.who || t("brain_picker.current_unknown")}
            {brain.model && <span className="font-normal text-muted-foreground">{` · ${brain.model}`}</span>}
          </span>
        </span>
      </p>
      <button
        type="button"
        data-testid="current-brain-change"
        onClick={() => openSettingsDestination("model", "brain")}
        className="inline-flex items-center gap-1.5 rounded-sm text-meta font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {t("brain_picker.change_in_settings")}
        <ArrowRight className="h-3.5 w-3.5" aria-hidden />
      </button>
    </div>
  );
}
