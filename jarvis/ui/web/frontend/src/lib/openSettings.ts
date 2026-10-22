/**
 * Open the Settings hub on one of its provider pages (MODEL, VOICE, or the
 * advanced "API keys & routing" page) from anywhere in the app.
 *
 * All three are the `apikeys` section; the page is the hub-local choice in
 * `store/settingsPage.ts`. Used by the sidebar's SETTINGS destination and by
 * every "this moved to Settings › Voice" link that replaced a duplicate
 * control.
 */
import { useEventStore } from "@/store/events";
import { useSettingsPage, type ProviderPage } from "@/store/settingsPage";
import { clearApiKeysTabRequest, requestApiKeysTab } from "@/lib/apiKeysTab";

export function openSettingsDestination(page: ProviderPage, tab: string | null = null): void {
  if (tab) requestApiKeysTab(tab);
  else clearApiKeysTabRequest();
  useSettingsPage.getState().open(page);
  useEventStore.getState().setActiveSection("apikeys");
}
