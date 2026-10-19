import { MotionConfig } from "framer-motion";
import { StarkShell } from "@/components/stark/StarkShell";
import { LiveCameras, LiveNews } from "@/components/stark/LivePanels";
import { CctvWall } from "@/components/stark/CctvWall";
import { NewsTicker } from "@/components/stark/WorldPanels";
import { useCctvCatalog } from "@/lib/cctv";
import { useLiveCatalog } from "@/lib/live";
import { fill, useT } from "@/i18n";

/**
 * LIVE: every live feed on one screen. News channels (one large or four at
 * once, world and Indian channels), public city cameras on YouTube, and the
 * public road and city cameras God's Eye View lists, as a wall of frames.
 */
export function LiveView() {
  const t = useT();
  const live = useLiveCatalog();
  const cctv = useCctvCatalog();
  const streams = live.data?.streams ?? [];
  const channels = streams.filter((s) => s.kind === "news").length;
  const cams = streams.length - channels + (cctv.data?.cameras.length ?? 0);
  return (
    <MotionConfig reducedMotion="user">
      <StarkShell
        screen="live"
        subtitle={t("stark.livescreen.subtitle")}
        badge={
          streams.length ? (
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground" data-testid="stark-live-totals">
              {fill(t("stark.livescreen.totals"), { channels, cameras: cams.toLocaleString() })}
            </p>
          ) : undefined
        }
        testId="stark-live"
      >
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 pb-3 pt-1 xl:overflow-hidden xl:px-6">
          <div className="grid shrink-0 gap-3 xl:h-[52%] xl:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
            <LiveNews className="h-[24rem] xl:h-auto" />
            <LiveCameras className="h-[22rem] xl:h-auto" />
          </div>
          <CctvWall className="h-[26rem] shrink-0 xl:h-auto xl:min-h-0 xl:flex-1" />
          <div className="shrink-0">
            <NewsTicker />
          </div>
        </div>
      </StarkShell>
    </MotionConfig>
  );
}
