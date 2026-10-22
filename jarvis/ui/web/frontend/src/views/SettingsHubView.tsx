import * as Dialog from "@radix-ui/react-dialog";
import { SectionWindowButton } from "@/components/layout/SectionWindowButton";
import { isTourEvent } from "@/components/onboarding/tourEvents";
import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ComponentType, type LazyExoticComponent } from "react";
import { AudioLines, Brain, ChevronDown, Loader2, Search, X, type LucideIcon } from "lucide-react";
import {
  NAV_FOOTER_ITEMS,
  NAV_GROUPS,
  SETTINGS_HUB_ONLY_ITEMS,
  resolveNavLabel,
  type NavItem,
} from "@/components/layout/navGroups";
import { useEventStore } from "@/store/events";
import { useSettingsJump } from "@/store/settingsJump";
import { useSectionHealth } from "@/hooks/useProviders";
import { useT, useUiLanguage } from "@/i18n";
import { isComboboxPanelEvent } from "@/components/ui/combobox";
import { searchSettingsOptions, searchSettingsPages } from "@/views/settings/settingsSearch";
import { APIKEYS_TAB_EVENT, apiKeysHealthError, requestedApiKeysTab } from "@/lib/apiKeysTab";
import { cn } from "@/lib/utils";
import { pageForApiKeysTab, useSettingsPage, type ProviderPage } from "@/store/settingsPage";
import type { ApiKeysScope } from "@/views/ApiKeysView";

/**
 * The Settings hub — every personal/system section behind one full-window
 * page over the user's current section, with a searchable left navigation
 * and the selected section on the right:
 *
 *   (first)   Model, Voice — the two questions people come with
 *   General:  General, Keyboard shortcuts, Profile, {name} (the assistant)
 *   Activity: Spend, Feedback
 *   Advanced (folded): API Keys, Computers, Appshots, My Pets, Socials
 *
 * Model, Voice and API Keys are three faces of ONE section id, `apikeys`
 * (see store/settingsPage.ts): each provider tier is shown on exactly one
 * face, so no two controls set the same config (P1-G, 2026-10-11).
 *
 * Same merged-section pattern as VoiceHubView / ClisHubView: the active
 * section id IS the tab state, so deep links, voice commands ("open the API
 * keys"), the deck and detached windows keep landing on the right tab with no
 * extra routing. The merged-in ids ("telephony", "taskbar", "languages",
 * "telephony-setup") resolve to the tab that hosts their content today,
 * exactly like MainView used to map them to standalone views.
 *
 * Labels, icons and grouping resolve from `NAV_GROUPS` (via `resolveNavLabel`,
 * so all three locales behave exactly like the sidebar rows did) — no second hand-written list to drift (AP-4).
 * One exception: inside the hub the "settings" entry reads "General", because
 * the whole page already is Settings.
 *
 * Tab contents stay code-split one `lazy` boundary per view, so opening the
 * hub still only pays for the shell plus the visible tab.
 */

const SettingsTab = lazy(() =>
  import("@/views/SettingsView").then((m) => ({ default: m.SettingsView })),
);
const ProfileTab = lazy(() =>
  import("@/views/ProfileView").then((m) => ({ default: m.ProfileView })),
);
const AssistantTab = lazy(() =>
  import("@/views/AssistantProfileView").then((m) => ({ default: m.AssistantProfileView })),
);
const SocialsTab = lazy(() =>
  import("@/views/socials/SocialsView").then((m) => ({
    default: m.SocialsView,
  })),
);
const ApiKeysTab = lazy(() =>
  import("@/views/ApiKeysView").then((m) => ({
    default: (props: { scope?: ApiKeysScope }) => <m.ApiKeysView {...props} />,
  })),
);
const TelephonySetupTab = lazy(() =>
  import("@/views/TelephonyView").then((m) => ({
    default: m.TelephonySetupView,
  })),
);
const ComputersTab = lazy(() =>
  import("@/views/ComputersView").then((m) => ({ default: m.ComputersView })),
);
const AppshotsTab = lazy(() =>
  import("@/views/AppshotsView").then((m) => ({ default: m.AppshotsView })),
);
const ShortcutsTab = lazy(() =>
  import("@/views/ShortcutsView").then((m) => ({ default: m.ShortcutsView })),
);
const PetsTab = lazy(() =>
  import("@/views/PetsView").then((m) => ({ default: m.PetsView })),
);
const CostsTab = lazy(() =>
  import("@/views/CostsView").then((m) => ({ default: m.CostsView })),
);
const FeedbackTab = lazy(() =>
  import("@/views/feedback/FeedbackView").then((m) => ({
    default: m.FeedbackView,
  })),
);

/** The entries of the left navigation, in display order. */
type HubNavId =
  | "settings"
  | "shortcuts"
  | "appshots"
  | "pets"
  | "profile"
  | "agent-instructions"
  | "socials"
  | "apikeys"
  | "computers"
  | "costs"
  | "feedback";

/** The hub's first two entries: faces of the `apikeys` section, not ids of their own. */
type ProviderPageEntry = { page: Exclude<ProviderPage, "apikeys">; labelKey: string; icon: LucideIcon };
const PROVIDER_PAGE_ENTRIES: readonly ProviderPageEntry[] = [
  { page: "model", labelKey: "settings_pages.model_title", icon: Brain },
  { page: "voice", labelKey: "settings_pages.voice_title", icon: AudioLines },
];

const HUB_NAV_GROUPS: readonly { labelKey: string; ids: readonly HubNavId[]; advanced?: boolean }[] = [
  {
    labelKey: "settings_hub.group_general",
    ids: ["settings", "shortcuts", "profile", "agent-instructions"],
  },
  {
    labelKey: "settings_hub.group_activity",
    ids: ["costs", "feedback"],
  },
  {
    // Folded by default; opens by itself while one of its pages is shown.
    labelKey: "settings_pages.group_advanced",
    ids: ["apikeys", "computers", "appshots", "pets", "socials"],
    advanced: true,
  },
];

type HubContent = HubNavId | "telephony-setup" | "model" | "voice";
type HubHighlight = HubNavId | "model" | "voice";

const TAB_CONTENT: Record<Exclude<HubContent, "model" | "voice" | "apikeys">, LazyExoticComponent<ComponentType>> = {
  settings: SettingsTab,
  profile: ProfileTab,
  "agent-instructions": AssistantTab,
  socials: SocialsTab,
  "telephony-setup": TelephonySetupTab,
  computers: ComputersTab,
  appshots: AppshotsTab,
  shortcuts: ShortcutsTab,
  pets: PetsTab,
  costs: CostsTab,
  feedback: FeedbackTab,
};

/**
 * Which tab content — and which nav entry is highlighted — for an active
 * section id. Plain ids name their own tab; the merged-in ids resolve to the
 * tab hosting their content; anything else falls back to Settings.
 */
export function resolveHubTab(
  active: string,
  providerPage: ProviderPage = "model",
): { content: HubContent; highlight: HubHighlight } {
  switch (active) {
    case "profile":
      return { content: "profile", highlight: "profile" };
    case "agent-instructions":
      return { content: "agent-instructions", highlight: "agent-instructions" };
    case "socials":
      return { content: "socials", highlight: "socials" };
    case "apikeys":
      // One section, three faces — see store/settingsPage.ts.
      return { content: providerPage, highlight: providerPage };
    case "telephony":
      return { content: "apikeys", highlight: "apikeys" };
    case "telephony-setup":
      return { content: "telephony-setup", highlight: "apikeys" };
    case "computers":
      return { content: "computers", highlight: "computers" };
    case "appshots":
      return { content: "appshots", highlight: "appshots" };
    case "shortcuts":
      return { content: "shortcuts", highlight: "shortcuts" };
    case "pets":
      return { content: "pets", highlight: "pets" };
    case "costs":
      return { content: "costs", highlight: "costs" };
    case "feedback":
      return { content: "feedback", highlight: "feedback" };
    case "settings":
    case "taskbar":
    case "languages":
    default:
      return { content: "settings", highlight: "settings" };
  }
}

// `NAV_GROUPS` plus the footer: "feedback" lives in `NAV_FOOTER_ITEMS`, not in
// a group (same lookup as TopBar/DockRail) — without it the hub cannot resolve
// its own tenth entry.
const ALL_NAV_ITEMS: readonly NavItem[] = [
  ...NAV_GROUPS.flat(),
  ...NAV_FOOTER_ITEMS,
  ...SETTINGS_HUB_ONLY_ITEMS,
];

function findNavItem(id: HubNavId): NavItem {
  const item = ALL_NAV_ITEMS.find((row) => row.id === id);
  if (!item) throw new Error(`Settings hub nav item missing: ${id}`);
  return item;
}

function HubLoadingFallback() {
  return (
    <div
      className="flex h-full w-full items-center justify-center"
      role="status"
      aria-busy="true"
      data-testid="settings-hub-loading"
    >
      <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
    </div>
  );
}

export function SettingsHubView() {
  const t = useT();
  const language = useUiLanguage();
  const active = useEventStore((s) => s.activeSection);
  const solo = useEventStore((s) => s.solo);
  const setActive = useEventStore((s) => s.setActiveSection);
  const [query, setQuery] = useState("");
  const [searchTarget, setSearchTarget] = useState<string | null>(null);
  const { health: sectionHealth } = useSectionHealth();

  // A group picked in the quick switcher while the hub was closed (or open on
  // another tab) — take it over once; see store/settingsJump.
  const pendingJump = useSettingsJump((s) => s.target);
  useEffect(() => {
    if (pendingJump === null) return;
    const target = useSettingsJump.getState().take();
    if (target) setSearchTarget(target);
  }, [pendingJump]);

  // Which face of `apikeys` (Model / Voice / API Keys): an explicit pick
  // wins, else the tab someone asked for, else Model.
  const explicitPage = useSettingsPage((s) => s.page);
  const openPage = useSettingsPage((s) => s.open);
  useEffect(() => {
    const onRequest = (event: Event) => {
      openPage(pageForApiKeysTab((event as CustomEvent<string | null>).detail));
    };
    window.addEventListener(APIKEYS_TAB_EVENT, onRequest);
    return () => window.removeEventListener(APIKEYS_TAB_EVENT, onRequest);
  }, [openPage]);
  // Closing the hub forgets the pick, so the next "open API keys" from
  // elsewhere lands where its own request says.
  useEffect(() => () => openPage(null), [openPage]);
  // Read once: the API Keys page takes (and so clears) the request it was
  // opened with, and that must not flip the hub back to Model.
  const [requestedPage] = useState(() => pageForApiKeysTab(requestedApiKeysTab()));
  const providerPage = explicitPage ?? requestedPage;

  const { content, highlight } = resolveHubTab(active, providerPage);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const labelOf = (item: NavItem) =>
    item.id === "settings" ? t("settings_hub.general") : resolveNavLabel(t, item);
  const needle = query.trim().toLowerCase();
  const matches = (item: NavItem) =>
    needle === "" || labelOf(item).toLowerCase().includes(needle);

  // Twelve entries — filtered inline; no memo needed at this size.
  const visibleGroups = HUB_NAV_GROUPS.map((group) => ({
    ...group,
    items: group.ids.map(findNavItem).filter(matches),
  })).filter((group) => group.items.length > 0);
  const optionMatches = searchSettingsOptions(language, query, t);
  const pageMatches = searchSettingsPages(language, query, t)
    .filter((match) => !visibleGroups.some((group) =>
      group.items.some((item) => item.id === match.id)))
    .map((match) => ({ ...match, label: labelOf(findNavItem(match.id)) }));

  // The health signal the sidebar row used to carry, now on the hub's own
  // nav: a hard provider error on API Keys. Badge only, never a toast.
  const apikeysHasError = useMemo(() => apiKeysHealthError(sectionHealth), [sectionHealth]);

  const renderPageEntry = (entry: ProviderPageEntry) => {
    const Icon = entry.icon;
    const isActive = highlight === entry.page;
    const label = t(entry.labelKey);
    if (needle && !label.toLowerCase().includes(needle)) return null;
    return (
      <li key={entry.page}>
        <button
          type="button"
          data-testid={`settings-hub-nav-${entry.page}`}
          onClick={() => {
            setQuery("");
            setSearchTarget(null);
            openPage(entry.page);
            setActive("apikeys");
          }}
          aria-current={isActive ? "page" : undefined}
          className={cn(
            "group flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            isActive
              ? "bg-secondary font-medium text-foreground-strong"
              : "text-foreground-secondary hover:bg-secondary hover:text-foreground",
          )}
        >
          <Icon
            aria-hidden
            className={cn(
              "h-4 w-4 shrink-0 transition-colors",
              isActive ? "text-foreground-strong" : "text-muted-foreground group-hover:text-foreground",
            )}
          />
          <span className="min-w-0 flex-1 truncate text-left">{label}</span>
        </button>
      </li>
    );
  };

  const renderNavItem = (item: NavItem) => {
    const Icon = item.icon;
    const isActive = item.id === highlight;
    const showAlert = item.id === "apikeys" && apikeysHasError;
    const hint = showAlert ? t("sidebar.apikeys_alert") : undefined;
    return (
      <li key={item.id}>
        <button
          type="button"
          data-testid={`settings-hub-nav-${item.id}`}
          onClick={() => {
            setQuery("");
            setSearchTarget(null);
            // The API Keys page is a face of `apikeys` too.
            if (item.id === "apikeys") openPage("apikeys");
            setActive(item.id);
          }}
          title={hint}
          aria-current={isActive ? "page" : undefined}
          className={cn(
            "group flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            isActive
              ? "bg-secondary font-medium text-foreground-strong"
              : "text-foreground-secondary hover:bg-secondary hover:text-foreground",
          )}
        >
          <Icon
            aria-hidden
            className={cn(
              "h-4 w-4 shrink-0 transition-colors",
              isActive ? "text-foreground-strong" : "text-muted-foreground group-hover:text-foreground",
            )}
          />
          <span className="min-w-0 flex-1 truncate text-left">{labelOf(item)}</span>
          {showAlert && (
            <span
              data-testid="settings-hub-alert-apikeys"
              role="status"
              aria-label={t("sidebar.apikeys_alert")}
              className="h-2 w-2 shrink-0 rounded-full bg-destructive"
            />
          )}
        </button>
      </li>
    );
  };

  return (
    <div data-testid="settings-hub" className="flex h-full min-h-0 flex-col md:flex-row">
      <aside
        data-testid="settings-hub-sidebar"
        className="jarvis-nav-surface flex max-h-72 w-full shrink-0 flex-col border-b border-border md:max-h-none md:w-60 md:border-b-0"
      >
        <div className="px-3 pb-1 pt-3">
          {/* No way-out row of its own: the caption's back arrow and Escape
              leave the page, so the heading opens the nav. */}
          <div className="flex flex-wrap items-center justify-between gap-1 pb-3">
            <p className="px-2.5 pt-1 font-display text-lg font-semibold text-foreground-strong">
              {t("nav.settings")}
            </p>
            {!solo && <SectionWindowButton />}
          </div>
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <input type="text" role="searchbox" data-testid="settings-hub-search" value={query} onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Escape") setQuery(""); }}
              placeholder={t("settings_hub.search_placeholder")}
              aria-label={t("settings_hub.search_placeholder")}
              className="h-8 w-full rounded-md border border-transparent bg-secondary pl-8 pr-8 text-sm text-foreground placeholder:text-foreground-faint focus:border-accent focus:outline-none focus:ring-2 focus:ring-ring"
            />
            {query && <button type="button" onClick={() => setQuery("")}
              aria-label={t("settings_hub.clear_search")}
              className="absolute right-1 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <X className="h-3.5 w-3.5" aria-hidden />
            </button>}
          </div>
        </div>
        <nav aria-label={t("nav.settings")}
          className="min-h-0 flex-1 overflow-y-auto px-3 pb-5 scrollbar-jarvis">
          {/* MODEL and VOICE first: the two things people open Settings for. */}
          <ul className="space-y-0.5 pt-2" data-testid="settings-hub-primary">
            {PROVIDER_PAGE_ENTRIES.map(renderPageEntry)}
          </ul>
          <ul>
            {visibleGroups.map((group) => {
              if (!group.advanced) {
                return (
                  <li key={group.labelKey}>
                    <p className="px-2.5 pb-1.5 pt-5 text-xs font-medium text-muted-foreground">
                      {t(group.labelKey)}
                    </p>
                    <ul className="space-y-0.5">{group.items.map(renderNavItem)}</ul>
                  </li>
                );
              }
              // Searching unfolds it; so does showing one of its pages.
              const open =
                advancedOpen || needle !== "" || group.items.some((item) => item.id === highlight);
              return (
                <li key={group.labelKey}>
                  <button
                    type="button"
                    data-testid="settings-hub-advanced-toggle"
                    aria-expanded={open}
                    onClick={() => setAdvancedOpen(!open)}
                    className="flex w-full items-center gap-1.5 px-2.5 pb-1.5 pt-5 text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <ChevronDown aria-hidden className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")} />
                    {t(group.labelKey)}
                    {/* Folded, the group still says when a page inside needs care. */}
                    {!open && apikeysHasError && (
                      <span
                        data-testid="settings-hub-advanced-alert"
                        role="status"
                        aria-label={t("sidebar.apikeys_alert")}
                        className="ml-auto h-2 w-2 shrink-0 rounded-full bg-destructive"
                      />
                    )}
                  </button>
                  {open && (
                    <ul className="space-y-0.5" data-testid="settings-hub-advanced">
                      {group.items.map(renderNavItem)}
                    </ul>
                  )}
                </li>
              );
            })}
          </ul>
          {pageMatches.length > 0 && (
            <div data-testid="settings-hub-page-results">
              <p className="px-2.5 pb-1.5 pt-5 text-xs font-medium text-muted-foreground">
                {t("settings_hub.search_pages")}
              </p>
              <ul className="space-y-0.5">
                {pageMatches.map((match) => (
                  <li key={match.id}>
                    <button type="button" data-testid={`settings-hub-page-${match.id}`}
                      onClick={() => {
                        setSearchTarget(null);
                        setQuery("");
                        setActive(match.id);
                      }}
                      className="flex w-full flex-col rounded-md px-2.5 py-1.5 text-left transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                      <span className="text-sm font-medium text-foreground">{match.label}</span>
                      {match.detail && <span className="w-full truncate text-xs text-muted-foreground" title={match.detail}>{match.detail}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {optionMatches.length > 0 && (
            <div data-testid="settings-hub-option-results">
              <p className="px-2.5 pb-1.5 pt-5 text-xs font-medium text-muted-foreground">
                {t("settings_hub.search_results")}
              </p>
              <ul className="space-y-0.5">
                {optionMatches.map((match) => (
                  <li key={match.id}>
                    <button type="button" data-testid={`settings-hub-option-${match.id}`}
                      onClick={() => {
                        setSearchTarget(match.id);
                        setQuery("");
                        setActive("settings");
                      }}
                      className="flex w-full flex-col rounded-md px-2.5 py-1.5 text-left transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                      <span className="text-sm font-medium text-foreground">{match.label}</span>
                      {match.detail && <span className="w-full truncate text-xs text-muted-foreground" title={match.detail}>{match.detail}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {needle && visibleGroups.length === 0 && pageMatches.length === 0 && optionMatches.length === 0 && (
            <p role="status" className="px-2.5 py-5 text-sm text-muted-foreground">
              {t("settings_hub.no_results")}
            </p>
          )}
        </nav>
      </aside>
      <div className="jarvis-sheet relative flex min-h-0 min-w-0 flex-1 flex-col">
        <div data-testid="settings-hub-content" className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis">
          {/* One measure for every tab: each page runs the full width of this
              column with its header and body on the same left edge, and the
              column centres once a very wide window would stretch rows apart. */}
          <div data-testid="settings-hub-column" className="mx-auto h-full w-full max-w-[1440px]">
            <Suspense fallback={<HubLoadingFallback />}>
              {content === "settings" ? (
                <SettingsTab searchTarget={searchTarget} onSearchTargetHandled={() => setSearchTarget(null)} />
              ) : content === "model" || content === "voice" || content === "apikeys" ? (
                <ApiKeysTab key={content} scope={content === "apikeys" ? undefined : content} />
              ) : (
                (() => {
                  const Content = TAB_CONTENT[content];
                  return <Content />;
                })()
              )}
            </Suspense>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * The hub as a full-window page over the current section: it covers the app
 * sidebar and the stage, wears the same gray ground + rounded reading sheet as
 * the app shell, and starts BELOW the 32 px window caption, so back/forward,
 * the drag strip and the window controls stay visible and live. The app root
 * is `isolate`, so the caption's own z-index cannot lift it over this portal;
 * leaving the strip uncovered is the only way it shows. Closing it — the
 * caption's back arrow or Escape — returns to the section behind it.
 *
 * Positioned with fixed insets rather than a transform: a transform would turn
 * the dialog into the containing block of every `position: fixed` layer a tab
 * renders inline (an image preview, view-level dialogs) and trap them inside
 * the page instead of covering the screen.
 */
export function SettingsHubDialog({ onClose }: { onClose: () => void }) {
  const t = useT();
  const content = useRef<HTMLDivElement>(null);
  const opener = useRef(document.activeElement);
  // A nested modal inside a tab (credential dialogs, pickers) owns outside
  // clicks and Escape while it is open; so does an open combobox panel.
  // The first-run guide dims the window over this dialog and points into it;
  // a click on its card or its dim must not read as "outside" and close the
  // very page it is pointing at.
  // The window caption stays live above the page: its controls minimise,
  // maximise or drag the window, which must not close the hub.
  const nestedOwnsEvent = (event: Event) =>
    isComboboxPanelEvent(event) ||
    (event.target instanceof Element && event.target.closest('[data-testid="window-caption"]') != null) ||
    content.current?.querySelector('[aria-modal="true"]') != null ||
    isTourEvent(event);
  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-x-0 bottom-0 top-8 z-40 jarvis-nav-surface" />
        <Dialog.Content
          data-testid="settings-hub-dialog"
          ref={content}
          aria-describedby={undefined}
          onCloseAutoFocus={(event) => {
            const previous = opener.current;
            const target = previous instanceof HTMLElement && previous.isConnected && previous !== document.body
              ? previous
              : document.querySelector<HTMLElement>("main");
            if (!target) return;
            // This route-driven dialog has no Radix Trigger to restore focus to.
            event.preventDefault();
            const needsTabIndex = target.tagName === "MAIN" && !target.hasAttribute("tabindex");
            if (needsTabIndex) target.setAttribute("tabindex", "-1");
            target.focus({ preventScroll: true });
            if (needsTabIndex) target.removeAttribute("tabindex");
          }}
          onPointerDownOutside={(event) => { if (nestedOwnsEvent(event)) event.preventDefault(); }}
          onFocusOutside={(event) => { if (nestedOwnsEvent(event)) event.preventDefault(); }}
          onInteractOutside={(event) => { if (nestedOwnsEvent(event)) event.preventDefault(); }}
          onEscapeKeyDown={(event) => {
            if (content.current?.querySelector('[aria-modal="true"]')) {
              event.preventDefault();
              return;
            }
            // Escape in a filled search box clears the search first.
            const target = event.target;
            if (target instanceof HTMLInputElement && target.dataset.testid === "settings-hub-search" && target.value) {
              event.preventDefault();
            }
          }}
          className="jarvis-nav-surface fixed inset-x-0 bottom-0 top-8 z-40 flex flex-col overflow-hidden text-foreground outline-none"
        >
          <Dialog.Title className="sr-only">{t("nav.settings")}</Dialog.Title>
          <SettingsHubView />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
