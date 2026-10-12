import { useState } from "react";
import { ArrowRightLeft, Loader2, Settings2 } from "lucide-react";

import { useVoiceEngineDisplay } from "@/hooks/useVoiceEngineDisplay";
import { useVoiceMode } from "@/hooks/useVoiceMode";
import { saveBrainProviderModel, switchBrainProvider } from "@/hooks/useProviders";
import { fill, useT } from "@/i18n";
import { requestApiKeysTab } from "@/lib/apiKeysTab";
import { resolveVoiceContinuity, type ChatPick } from "@/lib/voiceContinuity";
import { useEventStore } from "@/store/events";

/**
 * The line above Jarvis' voice stage that says which engine answers a spoken
 * turn, and how that relates to the provider picked for the typed chat.
 *
 * It replaces a static "voice runs on a different brain" sentence that left
 * the person to discover, mid-call, that a chat showing "Hermes Agent" talked
 * through Gemini Live. Nothing here changes a setting by itself: a provider
 * Jarvis' brain can drive gets an explicit "Use … for voice" button (which
 * also moves voice to Pipeline when it was on a realtime provider, and says
 * so first); a CLI agent gets the concrete reason it cannot hold a spoken
 * conversation and a way to the voice settings.
 */
export function VoiceContinuityNotice({ chat }: { chat: ChatPick | null }) {
  const t = useT();
  const engine = useVoiceEngineDisplay();
  const { setModeAsync } = useVoiceMode();
  const pushToast = useEventStore((s) => s.pushToast);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const [busy, setBusy] = useState(false);

  const state = resolveVoiceContinuity(chat, engine);
  const modeName = t(
    engine.tier === "realtime" ? "apikeys_view.mode_realtime" : "apikeys_view.mode_pipeline",
  );
  const voiceName = engine.model
    ? `${engine.providerLabel} · ${engine.model}`
    : engine.providerLabel;
  const vars = { chat: chat?.label ?? "", voice: voiceName, mode: modeName };

  const openSettings = (tab: "brain" | "realtime") => {
    requestApiKeysTab(tab);
    setActiveSection("apikeys");
  };

  const useForVoice = async () => {
    if (state.kind !== "switchable" || busy) return;
    const target = state.chat;
    setBusy(true);
    try {
      // Brain first: if the provider cannot run (no key, not installed) the
      // backend refuses here and the voice mode is left exactly as it was.
      await switchBrainProvider(target.id);
      if (target.model) await saveBrainProviderModel(target.id, target.model);
      window.dispatchEvent(new CustomEvent("jarvis:brain-switched"));
      if (state.needsPipeline) await setModeAsync("pipeline");
      pushToast("success", fill(t("society.chat.voice_switched"), { chat: target.label }));
    } catch (err) {
      const detail = err instanceof Error && err.message ? err.message : "";
      pushToast(
        "error",
        `${fill(t("society.chat.voice_switch_failed"), { chat: target.label })}${detail ? ` ${detail}` : ""}`,
      );
    } finally {
      setBusy(false);
    }
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
            onClick={() => void useForVoice()}
            disabled={busy}
            data-testid="voice-continuity-use"
            title={fill(t("society.chat.voice_use_hint"), vars)}
            className="inline-flex h-7 items-center gap-1.5 rounded-full bg-foreground px-3 font-medium text-background transition-colors hover:bg-foreground/85 disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <ArrowRightLeft className="h-3.5 w-3.5" aria-hidden />
            )}
            {fill(t("society.chat.voice_use"), vars)}
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
