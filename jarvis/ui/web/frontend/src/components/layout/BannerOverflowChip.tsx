import { useT, fill } from "@/i18n";
import type { BannerSlot } from "@/store/bannerSlot";

/**
 * The "+N" on the banner that holds the slot: the other active banners, folded.
 * Pressing it unfolds them under this one; pressing it again folds them back.
 * Renders nothing on a banner that is alone or not on top.
 */
export function BannerOverflowChip({ slot }: { slot: BannerSlot }) {
  const t = useT();
  if (!slot.top || slot.others === 0) return null;
  const label = slot.expanded
    ? t("banner_slot.show_fewer")
    : fill(t("banner_slot.more"), { count: slot.hiddenCount });
  return (
    <button
      type="button"
      data-testid="banner-overflow-chip"
      onClick={slot.toggleExpanded}
      aria-expanded={slot.expanded}
      className="shrink-0 self-center rounded-full border border-border px-2 py-0.5 text-micro font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {label}
    </button>
  );
}
