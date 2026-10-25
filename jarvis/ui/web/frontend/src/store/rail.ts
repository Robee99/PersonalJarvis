import { create } from "zustand";

/**
 * The right-hand rail's own switches (components/rail/JarvisRail.tsx): which
 * figure sits in the ring, whether hands steer the brain map (HOLO), whether
 * the reflex list is open, and whether the rail is folded to its ring.
 *
 * Remembered per browser; without storage they start at the defaults.
 */
export type RailAvatar = "ring" | "cube" | "face";

interface RailState {
  avatar: RailAvatar;
  holo: boolean;
  reflexOpen: boolean;
  /** Off the Stark screens the rail starts folded to its ring. */
  folded: boolean;
  /** The brain map is open over a Stark screen (StarkShell says so). */
  mapOpen: boolean;
  setAvatar: (avatar: RailAvatar) => void;
  setHolo: (on: boolean) => void;
  setReflexOpen: (open: boolean) => void;
  setFolded: (folded: boolean) => void;
  setMapOpen: (open: boolean) => void;
}

const KEY = "jarvis.rail.v1";

type Saved = Pick<RailState, "avatar" | "reflexOpen" | "folded">;

function load(): Partial<Saved> {
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Partial<Saved>) : {};
  } catch {
    // No storage (private window, blocked site data): the defaults.
    return {};
  }
}

function save(state: Saved) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    // Remembering the rail is a convenience; without storage it resets.
  }
}

const saved = load();

export const useRailStore = create<RailState>((set, get) => {
  const persist = () => {
    const { avatar, reflexOpen, folded } = get();
    save({ avatar, reflexOpen, folded });
  };
  return {
    avatar: saved.avatar === "cube" || saved.avatar === "face" ? saved.avatar : "ring",
    // Hands use the camera, so HOLO always starts off.
    holo: false,
    reflexOpen: Boolean(saved.reflexOpen),
    folded: saved.folded ?? true,
    mapOpen: false,
    setAvatar: (avatar) => {
      set({ avatar });
      persist();
    },
    setHolo: (holo) => set({ holo }),
    setReflexOpen: (reflexOpen) => {
      set({ reflexOpen });
      persist();
    },
    setFolded: (folded) => {
      set({ folded });
      persist();
    },
    setMapOpen: (mapOpen) => set({ mapOpen }),
  };
});
