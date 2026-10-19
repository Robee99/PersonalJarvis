import { create } from "zustand";
import { DEFAULT_FILTERS, KINDS, type Category, type Evidence, type IntelFilters } from "@/lib/intel";

/**
 * What the World screen is showing: the incident filters and the selected
 * incident. Shared by the incident panel, God's Eye View and the built-in
 * globe, and changed by the person or by the assistant (`WorldIntelCommand`
 * over the WebSocket), so all of them always agree.
 */
export type SelectVia = "panel" | "globe" | "assistant";

interface IntelStore {
  filters: IntelFilters;
  selectedId: string | null;
  selectedVia: SelectVia | null;
  /** Bumped whenever the globe should fly to the selection. */
  flySeq: number;
  /** Bumped by "back to the whole globe". */
  resetSeq: number;
  setFilters: (patch: Partial<IntelFilters>) => void;
  select: (id: string, via: SelectVia, fly?: boolean) => void;
  clearSelection: () => void;
  resetView: () => void;
  applyCommand: (payload: unknown) => void;
}

const EVIDENCE: Evidence[] = ["measured", "agency", "media"];

function csv<T extends string>(value: unknown, allowed: readonly T[]): T[] {
  if (typeof value !== "string" || !value) return [];
  return value
    .split(",")
    .map((part) => part.trim())
    .filter((part): part is T => (allowed as readonly string[]).includes(part));
}

function text(value: unknown, max = 200): string {
  return typeof value === "string" ? value.slice(0, max) : "";
}

export const useIntelStore = create<IntelStore>((set) => ({
  filters: DEFAULT_FILTERS,
  selectedId: null,
  selectedVia: null,
  flySeq: 0,
  resetSeq: 0,
  setFilters: (patch) => set((s) => ({ filters: { ...s.filters, ...patch } })),
  select: (id, via, fly = true) =>
    set((s) => ({ selectedId: id, selectedVia: via, flySeq: fly ? s.flySeq + 1 : s.flySeq })),
  clearSelection: () => set({ selectedId: null, selectedVia: null }),
  resetView: () =>
    set((s) => ({ filters: DEFAULT_FILTERS, selectedId: null, selectedVia: null, resetSeq: s.resetSeq + 1 })),
  applyCommand: (payload) => {
    if (!payload || typeof payload !== "object") return;
    const p = payload as Record<string, unknown>;
    const action = text(p.action, 10);
    if (action === "focus") {
      const id = text(p.event_id);
      if (id) set((s) => ({ selectedId: id, selectedVia: "assistant", flySeq: s.flySeq + 1 }));
    } else if (action === "filter") {
      const hours = typeof p.hours === "number" && p.hours > 0 ? Math.min(p.hours, 720) : null;
      const severity =
        typeof p.min_severity === "number" && p.min_severity >= 0 ? Math.min(4, Math.round(p.min_severity)) : null;
      const category = text(p.category, 10);
      set({
        filters: {
          hours,
          kinds: csv(p.kinds, KINDS),
          category: category === "hazard" || category === "unrest" ? (category as Category) : null,
          region: text(p.region, 40) || null,
          minSeverity: severity,
          evidence: csv(p.evidence, EVIDENCE),
        },
      });
    } else if (action === "reset") {
      set((s) => ({ filters: DEFAULT_FILTERS, selectedId: null, selectedVia: null, resetSeq: s.resetSeq + 1 }));
    }
  },
}));
