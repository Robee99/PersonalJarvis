import { KeyRound, Mic, Wand2 } from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { useT } from "@/i18n";
import { openSettingsDestination } from "@/lib/openSettings";
import { MovedSettingLink } from "@/views/settings/MovedSettingLink";

export interface VoiceApiKeysTabProps {
  /**
   * Suppress this view's own `ViewHeader`.
   *
   * Set by the merged voice section, which renders one "{name} Voice" header
   * above the tab bar — a second bordered band right below it reads as a
   * rendering fault. Standalone rendering keeps its own header.
   */
  hideHeader?: boolean;
}

/**
 * "API Keys" tab of the merged voice section — where the providers that turn
 * speech into text are picked.
 *
 * Since 2026-10-11 it holds no picker of its own: speech-to-text is chosen in
 * Settings › Voice and the optional wording pass under Settings › Advanced ›
 * API keys & routing. The tab stays (its section id, `voice-api-keys`, is a
 * voice-navigation target) and links to both.
 *
 * The Realtime|Pipeline engine switch deliberately does NOT ride along
 * (maintainer feedback 2026-07-29). This section is about turning speech into
 * text; picking the voice ENGINE is a different decision, and it already has
 * one home with the full explanatory context next to it — the API-Keys view's
 * `EngineModeSwitch` + `VoiceEngineContext`. A second switch here read as a
 * stray control in a section that never asked the question. Do not add it back.
 */
export function VoiceApiKeysTab({ hideHeader = false }: VoiceApiKeysTabProps = {}) {
  const t = useT();

  // This tab used to render the speech-to-text (`stt`) and wording
  // (`dictation`) provider pickers a second time, beside their copies on the
  // API-Keys page — two controls for one config each (UI audit 2026-10-07,
  // P0-3). Each lives in ONE place now; this tab sends people there.
  return (
    <div className="flex h-full min-h-0 flex-col">
      {!hideHeader && (
        <ViewHeader
          icon={<KeyRound className="h-4 w-4 text-foreground" />}
          title={t("voice.api_keys.title")}
          subtitle={t("voice.api_keys.description")}
        />
      )}
      <div
        className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis p-6"
        data-testid="voice-api-keys-tab"
      >
        <div className="mx-auto flex w-full max-w-page flex-col gap-3">
          <MovedSettingLink
            testId="voice-api-keys-stt-link"
            icon={<Mic />}
            title={t("apikeys_view.tab_stt")}
            description={t("settings_pages.stt_moved")}
            actionLabel={t("settings_pages.open_voice")}
            onOpen={() => openSettingsDestination("voice", "stt")}
          />
          <MovedSettingLink
            testId="voice-api-keys-wording-link"
            icon={<Wand2 />}
            title={t("apikeys_view.tab_dictation")}
            description={t("settings_pages.wording_moved")}
            actionLabel={t("settings_pages.open_apikeys")}
            onOpen={() => openSettingsDestination("apikeys", "dictation")}
          />
        </div>
      </div>
    </div>
  );
}
