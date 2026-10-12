/**
 * One banner slot for the whole window.
 *
 * The admin-rights notice, the voice warm-up band and the one-time "all set"
 * note used to stack above the content and together took a quarter to a third
 * of the window (forensic UI audit 2026-10-07, P1-7). Each banner still decides
 * whether it has something to say; the slot decides who says it: only the
 * highest-priority active banner renders, and it carries a "+N" chip that
 * unfolds the others. The status line in the sidebar keeps reporting the
 * voice state either way, so a folded warm-up band loses nothing.
 */
import { useEffect } from "react";
import { create } from "zustand";

/** Priorities of the window banners. Higher wins the slot. */
export const BANNER_PRIORITY = {
  /** Outside input software cannot reach the window: something is broken. */
  inputIsolation: 30,
  /** The voice stack is still starting: transient progress. */
  voiceWarming: 20,
  /** The one-time "everything is set up" note: good news, never urgent. */
  readyNote: 10,
} as const;

interface BannerSlotState {
  /** Active banners by id → priority. */
  active: Record<string, number>;
  expanded: boolean;
  setActive: (id: string, priority: number | null) => void;
  toggleExpanded: () => void;
}

export const useBannerSlotStore = create<BannerSlotState>((set) => ({
  active: {},
  expanded: false,
  setActive: (id, priority) =>
    set((state) => {
      const has = id in state.active;
      if (priority === null) {
        if (!has) return state;
        const next = { ...state.active };
        delete next[id];
        // Nothing left to unfold: fold back so the next stack starts compact.
        return { active: next, expanded: Object.keys(next).length > 1 && state.expanded };
      }
      if (has && state.active[id] === priority) return state;
      return { active: { ...state.active, [id]: priority } };
    }),
  toggleExpanded: () => set((state) => ({ expanded: !state.expanded })),
}));

/** Ids ordered by priority, highest first (ties by id, so the order is stable). */
export function rankBanners(active: Record<string, number>): string[] {
  return Object.keys(active).sort((a, b) => active[b] - active[a] || a.localeCompare(b));
}

export interface BannerSlot {
  /** Render the banner now. */
  visible: boolean;
  /** This banner holds the slot (the chip belongs to it). */
  top: boolean;
  /** Other active banners folded behind this one (0 when expanded or not top). */
  hiddenCount: number;
  /** Other active banners at all, folded or not. */
  others: number;
  expanded: boolean;
  toggleExpanded: () => void;
}

/**
 * Register a banner with the slot and learn whether it may render.
 *
 * The banner's own entry is counted immediately (not only after the effect
 * registered it), so a lone banner renders on its first paint.
 */
export function useBannerSlot(id: string, priority: number, active: boolean): BannerSlot {
  const setActive = useBannerSlotStore((s) => s.setActive);
  const registry = useBannerSlotStore((s) => s.active);
  const expanded = useBannerSlotStore((s) => s.expanded);
  const toggleExpanded = useBannerSlotStore((s) => s.toggleExpanded);

  useEffect(() => {
    setActive(id, active ? priority : null);
  }, [id, priority, active, setActive]);
  useEffect(() => () => setActive(id, null), [id, setActive]);

  const entries = { ...registry };
  if (active) entries[id] = priority;
  else delete entries[id];
  const ranked = rankBanners(entries);
  const top = active && ranked[0] === id;
  const others = Math.max(0, ranked.length - 1);
  return {
    visible: active && (top || expanded),
    top,
    hiddenCount: top && !expanded ? others : 0,
    others,
    expanded,
    toggleExpanded,
  };
}
