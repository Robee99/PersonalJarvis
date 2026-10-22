import { ArrowRight, Settings2 } from "lucide-react";

import { useVoiceEngineDisplay } from "@/hooks/useVoiceEngineDisplay";
import { fill, useT } from "@/i18n";
import { openSettingsDestination } from "@/lib/openSettings";
import { resolveVoiceContinuity, type ChatPick } from "@/lib/voiceContinuity";
import { pageForApiKeysTab } from "@/store/settingsPage";

/**
 * The line above Jarvis' voice stage that says which engine answers a spoken
 * turn, and how that relates to the provider picked for the typed chat.
 *
 * It replaces a static "voice runs on a different brain" sentence that left
 * the person to discover, mid-call, that a chat showing "Hermes Agent" talked
 * through Gemini Live. Nothing here changes a setting: the brain is chosen
 * in one place, the brain picker on Settings › Model, so a provider Jarvis'
 * brain can drive gets a link there, and a CLI agent gets the concrete reason
 * it cannot hold a spoken conversation and a way to the settings.
 */
export function VoiceContinuityNotice({ chat }: { chat: ChatPick | null }) {
  const t = useT();
  const engine = useVoiceEngineDisplay();

  const state = resolveVoiceContinuity(chat, engine);
  const modeName = t(
    engine.tier === "realtime" ? "apikeys_view.mode_realtime" : "apikeys_view.mode_pipeline",
  );
  const voiceName = engine.model
    ? `${engine.providerLabel} · ${engine.model}`
    : engine.providerLabel;
  const vars = { chat: chat?.label ?? "", voice: voiceName, mode: modeName };

  const openSettings = (tab: "brain" | "realtime") => {
    openSettingsDestination(pageForApiKeysTab(tab), tab);
  };

  let message: string;
  if (state.kind === "same") {
    message = fill(t("society.chat.voice_same"), vars);
  } else if (state.kind === "switchable") {
    message = fill(
      t(state.needsPipeline ? "society.chat.voice_differs_realtime" : "society.chat.voice_differs"),
      vars,
    );
  } else if (state.kind === "cli") {
    message = fill(t("society.chat.voice_cli"), vars);
    if (state.chat.id === "hermes") message = `${message} ${t("society.chat.voice_cli_hermes")}`;
  } else {
    message = fill(t("society.chat.voice_engine"), vars);
  }

  return (
    <div
      role="status"
      data-testid="voice-continuity"
      data-kind={state.kind}
      className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border bg-card px-3 py-2 text-xs text-muted-foreground"
    >
      <p className="min-w-0 flex-1 leading-relaxed">{message}</p>
      <div className="flex shrink-0 items-center gap-1.5">
        {state.kind === "switchable" ? (
          <button
            type="button"
            onClick={() => openSettings("brain")}
            data-testid="voice-continuity-use"
            title={fill(t("society.chat.voice_use_hint"), vars)}
            className="inline-flex h-7 items-center gap-1.5 rounded-full bg-foreground px-3 font-medium text-background transition-colors hover:bg-foreground/85 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {fill(t("society.chat.voice_use"), vars)}
            <ArrowRight className="h-3.5 w-3.5" aria-hidden />
          </button>
        ) : null}
        <button
          type="button"
          onClick={() =>
            openSettings(state.kind === "cli" || engine.tier === "pipeline" ? "brain" : "realtime")
          }
          data-testid="voice-continuity-settings"
          className="inline-flex h-7 items-center gap-1.5 rounded-full border border-border px-3 font-medium text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Settings2 className="h-3.5 w-3.5" aria-hidden />
          {t(state.kind === "cli" ? "society.chat.voice_pick_brain" : "society.chat.voice_settings")}
        </button>
      </div>
    </div>
  );
}
