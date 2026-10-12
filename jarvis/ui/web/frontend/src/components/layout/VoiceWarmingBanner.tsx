import { Loader2 } from "lucide-react";
import { useT } from "@/i18n";
import { useAssistantStatus } from "@/hooks/useAssistantStatus";
import { BANNER_PRIORITY, useBannerSlot } from "@/store/bannerSlot";
import { BannerOverflowChip } from "@/components/layout/BannerOverflowChip";

/**
 * Show the startup status only while voice is unavailable. Readiness is
 * shared with the sidebar and chat; completing startup clears this strip.
 */
export function VoiceWarmingBanner() {
  const t = useT();
  // The ONE status source (hooks/useAssistantStatus) shared with the sidebar
  // dot and the HUD header and ring, so no surface can say "Ready" while this
  // band says the assistant is starting up.
  const status = useAssistantStatus();
  const warming = status.voice === "starting" || status.voice === "booting";

  // One banner slot for the window (store/bannerSlot): a higher-priority
  // banner folds this one behind its "+N".
  const slot = useBannerSlot("voice-warming", BANNER_PRIORITY.voiceWarming, warming);
  if (!slot.visible) return null;

  return (
    <div
      data-testid="voice-warming-banner"
      data-state="warming"
      role="status"
      aria-live="polite"
      // No wash. The banner spans the whole window, and a full-bleed region
      // never rises off the page it sits on — so the state is carried by the
      // GLYPH's colour and the hairline under it, the way a status is meant to
      // be, instead of by a tinted slab across the top of the app.
      className="flex items-center gap-3 border-b border-border px-4 py-2.5"
    >
      <Loader2 className="h-4 w-4 shrink-0 animate-spin text-warning" aria-hidden />
      <span className="min-w-0 flex-1 text-body font-medium text-foreground-strong">
        {t("voice_state.warming_title")}
      </span>
      <BannerOverflowChip slot={slot} />
    </div>
  );
}
