import { MotionConfig } from "framer-motion";
import { StarkShell } from "@/components/stark/StarkShell";
import { LiveNews } from "@/components/stark/LivePanels";
import { NewsTicker } from "@/components/stark/WorldPanels";
import { useLiveCatalog } from "@/lib/live";
import { fill, useT } from "@/i18n";

/**
 * LIVE: the news. Live news channels, one large or four at once, in their
 * groups, with the headline ticker under them. Public cameras, from YouTube
 * and from God's Eye View, are on the Ghost screen's camera wall.
 */
export function LiveView() {
  const t = useT();
  const live = useLiveCatalog();
  const channels = (live.data?.streams ?? []).filter((s) => s.kind === "news").length;
  return (
    <MotionConfig reducedMotion="user">
      <StarkShell
        screen="live"
        subtitle={t("stark.livescreen.subtitle")}
        badge={
          channels ? (
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground" data-testid="stark-live-totals">
              {fill(t("stark.livescreen.channels"), { channels })}
            </p>
          ) : undefined
        }
        testId="stark-live"
      >
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 pb-3 pt-1 xl:overflow-hidden xl:px-6">
          <LiveNews className="h-[34rem] shrink-0 xl:h-auto xl:min-h-0 xl:flex-1" />
          <div className="shrink-0">
            <NewsTicker />
          </div>
        </div>
      </StarkShell>
    </MotionConfig>
  );
}
