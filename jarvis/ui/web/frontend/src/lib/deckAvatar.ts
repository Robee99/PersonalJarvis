import { useSyncExternalStore } from "react";

/**
 * What stands in the centre of the deck: Gigi, or the reactor ring.
 *
 * The reactor is the default since the Stark HUD (HUD v4, 2026-10-18: the
 * owner's concept boards put an arc reactor in the centre). Gigi, the
 * product's mascot, is one press away in the header. Both read the same `--orb-level` the orb root writes, so they move
 * with the voice the same way.
 *
 * Persisted in localStorage, like the deck/classic switch (lib/deckMode.ts).
 * A broken or absent value falls back to the reactor.
 */

export type DeckAvatar = "gigi" | "reactor";

const STORAGE_KEY = "deck.avatar.v1";
const DEFAULT_AVATAR: DeckAvatar = "reactor";
const listeners = new Set<() => void>();

function isDeckAvatar(v: unknown): v is DeckAvatar {
  return v === "gigi" || v === "reactor";
}

export function readDeckAvatar(): DeckAvatar {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return isDeckAvatar(raw) ? raw : DEFAULT_AVATAR;
  } catch {
    // Private mode / storage disabled: the deck still has to render.
    return DEFAULT_AVATAR;
  }
}

export function writeDeckAvatar(avatar: DeckAvatar): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, avatar);
  } catch {
    // Not being able to remember the choice must not stop the switch below.
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The current avatar; every caller re-renders when it is switched. */
export function useDeckAvatar(): DeckAvatar {
  return useSyncExternalStore(subscribe, readDeckAvatar, () => DEFAULT_AVATAR);
}
