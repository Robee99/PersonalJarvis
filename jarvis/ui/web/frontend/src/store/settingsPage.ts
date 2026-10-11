/**
 * Which face of the provider settings the Settings hub shows.
 *
 * The provider screen used to be one page ("API Keys & Providers") with three
 * layers of navigation, and the two questions people actually come with —
 * "which brain answers?" and "which voice do I hear / which ear listens?" —
 * sat in its fourth and fifth tab (forensic UI audit 2026-10-07, P0-3). The
 * hub now opens on MODEL and VOICE, and the rest of that screen lives under
 * Advanced as "API keys & routing".
 *
 * All three faces are the same section id, `apikeys`, on purpose: deep links,
 * voice navigation ("open the API keys"), the onboarding tour and every
 * "set up a provider" button already land on that id, and the navigate tool's
 * parity test pins the section list. The face is a hub-local choice on top:
 *
 * - a hub nav click picks it explicitly (`open`);
 * - otherwise a tab someone asked for with `requestApiKeysTab` decides
 *   (`pageForApiKeysTab`): the brain tab is MODEL, the voice tiers are VOICE,
 *   everything else is the advanced page;
 * - with neither, the hub lands on MODEL — the same place the old page opened
 *   on (its first tab was the brain).
 */
import { create } from "zustand";

export type ProviderPage = "model" | "voice" | "apikeys";

/** The page that hosts an API-Keys tab id (see `views/ApiKeysView.tsx`). */
export function pageForApiKeysTab(tab: string | null | undefined): ProviderPage {
  switch (tab) {
    case null:
    case undefined:
    case "":
    case "brain":
      return "model";
    case "tts":
    case "stt":
    case "realtime":
      return "voice";
    default:
      return "apikeys";
  }
}

interface SettingsPageState {
  /** Explicit choice; null means "derive from the requested tab". */
  page: ProviderPage | null;
  open: (page: ProviderPage | null) => void;
}

export const useSettingsPage = create<SettingsPageState>((set) => ({
  page: null,
  open: (page) => set({ page }),
}));
