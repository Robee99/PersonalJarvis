/**
 * The app's section list — the ONE source of truth for what sections exist.
 *
 * Extracted from `Sidebar.tsx` so the mission deck can show every section at
 * once without pulling the sidebar's own dependency tree (voice hooks, the
 * realtime control, provider health) into the entry chunk with it. The deck
 * ships in that chunk, and `MainView` keeps it deliberately small.
 *
 * A second hand-written list anywhere would be the classic drift trap (AP-4):
 * a section added here would silently never appear on the deck.
 */
import { Briefcase, Gauge, KeyRound, Swords, type LucideIcon } from "lucide-react";
import {
  AgentsIcon,
  ArtifactsIcon,
  BoardIcon,
  CaptureIcon,
  CodeIcon,
  ComputersIcon,
  DocsIcon,
  ExtensionsIcon,
  FeedbackIcon,
  AssistantIcon,
  MarketplaceIcon,
  MicrophoneIcon,
  PetsIcon,
  ProfileIcon,
  SettingsIcon,
  ShortcutsIcon,
  SocialsIcon,
  SpeechIcon,
  SpendIcon,
  TerminalIcon,
  WikiIcon,
} from "@/components/icons/sectionIcons";
import type { SectionId } from "@/store/events";
import type { HomeSurface } from "@/lib/homeSurface";

// Resolve a nav row's label, preferring the active-locale translation and
// falling back to the English `fallbackLabel` when the key is not yet present
// (the i18n resolver returns the key itself on a miss).
export function resolveNavLabel(t: (key: string) => string, item: NavItem): string {
  const resolved = t(item.labelKey);
  return resolved === item.labelKey && item.fallbackLabel ? item.fallbackLabel : resolved;
}

/**
 * The ids that all open the HUD — the ONE front page (2026-10-12).
 *
 * The chat, the HUD (mission deck) and the memory orb used to be three
 * sidebar rows and three separate pages; the owner wanted them in one place.
 * The HUD now carries the memory orb, the live voice orb and the real chat
 * thread side by side, and every id that used to name one of the three still
 * resolves — deep links, voice navigation ("open chat", "open the memory
 * orb"), the detached-window registry and the navigate parity tests keep
 * working — but all of them land on the same view.
 */
export const HUD_SECTION_IDS: readonly SectionId[] = ["chats", "deck", "orb"];

/** Is `section` one of the ids that open the HUD? */
export function isHudSection(section: string): boolean {
  return (HUD_SECTION_IDS as readonly string[]).includes(section);
}

/**
 * The front page is ONE section: the HUD, with the chat and the memory orb
 * inside it. The row is "HUD" whatever mode the chat is in — voice mode is a
 * state of the chat, not another place. Every other row passes through
 * unchanged. Pure, so the sidebar and the rail present the row identically;
 * the surface argument stays so callers need not change.
 */
export function presentNavItem(item: NavItem, _surface?: HomeSurface): NavItem {
  if (item.id !== "chats") return item;
  return { ...item, labelKey: "nav.hud", icon: Gauge, fallbackLabel: "HUD" };
}

export interface NavItem {
  id: SectionId;
  labelKey: string;
  icon: LucideIcon;
  // When set, the row is highlighted while the active section is any of these
  // ids — used by the merged section entries ("Skills & Tools" fronting
  // skills/plugins/mcps, "CLIs" fronting clis/cli-test-hub); the active id
  // doubles as the tab state.
  matchIds?: SectionId[];
  // English fallback shown when `labelKey` has no translation yet in the active
  // locale (the i18n resolver returns the key itself on a miss).
  fallbackLabel?: string;
  // Draws a small "Beta" pill after the label — the Agentic IDE runs real
  // coding-agent CLIs against the user's own filesystem, which is a step
  // riskier than the rest of the app, so the row says so up front.
  beta?: boolean;
}


/**
 * The sidebar's group labels, one per entry of `NAV_GROUPS`, by position.
 *
 * The first group (the front page) carries no label and is never collapsed.
 * Every other group is collapsible; `defaultOpen` is what a fresh install
 * shows, and the user's choice is remembered per group in localStorage.
 * "Tools" and "You" start folded so the column fits 1080 px without a
 * scrollbar with the defaults.
 */
export interface NavGroupMeta {
  id: string;
  labelKey?: string;
  fallbackLabel?: string;
  defaultOpen: boolean;
}

export const NAV_GROUP_META: readonly NavGroupMeta[] = [
  { id: "home", defaultOpen: true },
  {
    id: "workspace",
    labelKey: "nav.group_workspace",
    fallbackLabel: "Workspace",
    defaultOpen: true,
  },
  { id: "tools", labelKey: "nav.group_tools", fallbackLabel: "Tools", defaultOpen: false },
  { id: "you", labelKey: "nav.group_you", fallbackLabel: "You", defaultOpen: false },
  { id: "system", labelKey: "nav.group_system", fallbackLabel: "System", defaultOpen: true },
];

// Sidebar nav in four labelled groups (v4, 2026-09-02):
//   Workspace · Tools · You · System — preceded by the front page's own row.
// The render walks the groups in order, so the order below IS the on-screen
// order. Every section id is unchanged, so routing, deep links and the
// navigate parity tests do not move.
//
// Exported because the mission deck shows every section at once and jumps to
// them. A second hand-written list there would be the classic drift trap
// (AP-4): a section added here would silently never appear on the deck.
export const NAV_GROUPS: NavItem[][] = [
  // 0) The front page — the HUD: memory orb, voice orb and the chat in one
  // place. "deck" and "orb" are the same view under their old names, so the
  // row stays lit for them.
  [
    {
      id: "chats",
      labelKey: "nav.hud",
      icon: Gauge,
      fallbackLabel: "HUD",
      matchIds: [...HUD_SECTION_IDS],
    },
    // Every app the assistant can reach, as a wall of cards.
    { id: "armory", labelKey: "nav.armory", icon: Swords, fallbackLabel: "Tool armory" },
  ],
  // 1) Workspace — what the user builds with and reads back.
  [
    { id: "agents", labelKey: "nav.agents", icon: AgentsIcon },
    // The compact catalog opens on Plugins; direct section navigation selects
    // its corresponding tab and keeps this shared row highlighted.
    {
      id: "plugins",
      labelKey: "nav.extensions",
      icon: ExtensionsIcon,
      matchIds: ["skills", "plugins", "mcps"],
    },
    // The marketplace fills those lists: a plugin or a skill published there
    // ends up in one of them once installed.
    {
      id: "marketplace",
      labelKey: "nav.marketplace",
      icon: MarketplaceIcon,
      fallbackLabel: "Marketplace",
    },
    // Artifacts — everything a run produced. The id stays "visualization"
    // because it crosses the navigate parity test, the detachable-view
    // registry and deep links.
    {
      id: "visualization",
      labelKey: "nav.visualization",
      icon: ArtifactsIcon,
      fallbackLabel: "Artifacts",
    },
    { id: "board", labelKey: "nav.board", icon: BoardIcon },
    { id: "memory", labelKey: "nav.wiki", icon: WikiIcon },
    { id: "docs", labelKey: "nav.docs", icon: DocsIcon },
  ],
  // 2) Tools — the instruments: transcription, the run inspector, the CLIs
  // and the Agentic IDE (which puts real coding agents to work in a folder,
  // so its row says "Beta" up front).
  [
    { id: "sessions", labelKey: "nav.sessions", icon: MicrophoneIcon },
    // CLIs — the CLIs list + the CLI Test Hub behind one tab switch (CLIs first).
    { id: "clis", labelKey: "nav.clis_hub", icon: TerminalIcon, matchIds: ["clis", "cli-test-hub"] },
    {
      id: "agentic-ide",
      labelKey: "nav.agentic_ide",
      icon: CodeIcon,
      fallbackLabel: "Agentic IDE",
      // The classic grid is the same destination as far as the row is
      // concerned: someone who stepped back into it should still see where
      // they are in the navigation.
      matchIds: ["agentic-ide", "chat-workspace", "agentic-ide-classic"],
      beta: true,
    },
  ],
  // 3) You — what the assistant knows about the user, and the user's own
  // ledgers.
  [
    { id: "profile", labelKey: "nav.profile", icon: ProfileIcon },
    {
      id: "agent-instructions",
      labelKey: "nav.agent_instructions",
      icon: AssistantIcon,
      fallbackLabel: "Assistant",
    },
    // Spend & Tokens — every token the app spent, priced per provider, model
    // and role. It reports, it does not configure.
    { id: "costs", labelKey: "nav.costs", icon: SpendIcon, fallbackLabel: "Spend" },
    { id: "socials", labelKey: "nav.socials", icon: SocialsIcon },
  ],
  // 4) System. API Keys also fronts the former "Telephony" screen — the
  // telephony status/credentials/scripts/calls live as a section inside the
  // API-Keys view, so matchIds keeps this row highlighted when a "geh zur
  // Telefonie" voice command lands on the "telephony" id. Settings likewise
  // fronts the former "Taskbar" + "Languages" sections.
  [
    // Computers: the servers and virtual machines the assistant and its agents
    // can work on besides this one (a rented VPS, a hosting-account import, a
    // local VM). A Settings-hub entry, first under System.
    {
      id: "computers",
      labelKey: "nav.computers",
      icon: ComputersIcon,
      fallbackLabel: "Computers",
    },
    {
      id: "apikeys",
      labelKey: "nav.apikeys",
      icon: KeyRound,
      matchIds: ["apikeys", "telephony", "telephony-setup"],
    },
    {
      id: "settings",
      labelKey: "nav.settings",
      icon: SettingsIcon,
      matchIds: ["settings", "taskbar", "languages"],
    },
    // The voice section — dictation, the custom vocabulary, the keys that start
    // it, the dictation language and the speech-to-text providers — behind one
    // tab switch. "dictation" is the default landing.
    {
      id: "dictation",
      labelKey: "nav.voice",
      icon: SpeechIcon,
      matchIds: [
        "dictation",
        "dictionary",
        "voice-shortcuts",
        "voice-language",
        "voice-api-keys",
      ],
      // Name-FREE on purpose: the fallback is rendered verbatim when the key is
      // missing from a locale, and it is NOT interpolated.
      fallbackLabel: "Voice",
    },
  ],
];

/**
 * Feedback lives in the sidebar's footer beside the brain card, not in a
 * group: it is a door out of the product, not a section of it. Kept as a
 * `NavItem` so the deck and the rail can still list it from one definition.
 */
export const NAV_FOOTER_ITEMS: NavItem[] = [
  { id: "feedback", labelKey: "nav.feedback", icon: FeedbackIcon },
];

/**
 * Rows that exist only inside the Settings hub's own navigation, not in the
 * app sidebar: settings for one feature, reached through Settings the way the
 * Appshots page is.
 */
export const SETTINGS_HUB_ONLY_ITEMS: NavItem[] = [
  {
    id: "shortcuts",
    labelKey: "nav.shortcuts",
    icon: ShortcutsIcon,
    fallbackLabel: "Keyboard shortcuts",
  },
  { id: "appshots", labelKey: "nav.appshots", icon: CaptureIcon, fallbackLabel: "Appshots" },
  { id: "pets", labelKey: "nav.pets", icon: PetsIcon, fallbackLabel: "My Pets" },
];

/**
 * Every section id rendered inside the Settings hub (`SettingsHubView`).
 *
 * The ids keep their meaning — deep links, voice commands, the deck and the
 * detached-window registry all still name them — only the STAGE changed: the
 * router mounts the hub for any of them and the hub selects the matching tab.
 * Imported by the sidebar (which highlights its profile entry while one of
 * these is active and keeps them out of "Show more") and by the hub itself,
 * so the set is named exactly once.
 */
export const SETTINGS_HUB_IDS: readonly SectionId[] = [
  "settings",
  "taskbar",
  "languages",
  "profile",
  "agent-instructions",
  "socials",
  "apikeys",
  "telephony",
  "telephony-setup",
  "computers",
  "appshots",
  "shortcuts",
  "pets",
  "costs",
  "feedback",
];

/*
 * ── The five destinations (P1-G, 2026-10-11) ───────────────────────────────
 *
 * The sidebar used to show nine rows, six more under "More", footer icons and
 * fourteen hub pages: thirty doors, several of them to the same room (forensic
 * UI audit 2026-10-07, §2). The primary navigation is now exactly five
 * destinations — CHAT · ORB · AGENTS · WORK · SETTINGS — and every other
 * section is still one click away under a collapsed "Advanced" group.
 *
 * Nothing was renamed or removed: a destination is a VIEW over existing
 * section ids. Its row lands on `target`, lights up while any of `sections`
 * is on screen, and — where a destination spans several views — `tabs` draws
 * one tab bar over them (`DestinationTabs`). Section ids, deep links, the
 * detached-window registry and voice navigation (`NavigateSidebar`, pinned by
 * the navigate tool's parity test against `SECTION_IDS`) are unchanged.
 *
 * 2026-10-12: CHAT and ORB (and WORK's "Live" tab, the HUD) became ONE
 * destination — the HUD, which shows the memory orb, the voice orb and the
 * chat together. The primary navigation is four rows now: HUD · AGENTS ·
 * WORK · SETTINGS. The wiki notes ("memory") stay one click away under
 * Advanced.
 */

export type PrimaryNavKey = "chat" | "agents" | "work" | "settings";

export interface DestinationTab {
  /** The section the tab lands on. */
  id: SectionId;
  labelKey: string;
  fallbackLabel: string;
  /** Further ids that count as this tab (renamed ids still in circulation). */
  matchIds?: readonly SectionId[];
}

export interface PrimaryDestination {
  key: PrimaryNavKey;
  labelKey: string;
  fallbackLabel: string;
  icon: LucideIcon;
  /** Where a click on the row lands. */
  target: SectionId;
  /** Every section that belongs to this destination. */
  sections: readonly SectionId[];
  /** One tab bar over the destination's views; absent for a single view. */
  tabs?: readonly DestinationTab[];
  /** The onboarding tour's anchor on this row (defaults to `nav-<key>`). */
  tourAnchor?: string;
}

/** The Agentic IDE answers to three ids (it was renamed twice). */
const IDE_IDS: readonly SectionId[] = ["agentic-ide", "chat-workspace", "agentic-ide-classic"];

/**
 * WORK: everything that is about work being done or done — what runs
 * produced (Outputs), the coding workspace (Projects) and the activity board
 * (the run inspector was retired upstream; the live HUD is the front page
 * now). Routines live on each agent (AGENTS).
 */
export const WORK_TABS: readonly DestinationTab[] = [
  { id: "visualization", labelKey: "nav5.work_outputs", fallbackLabel: "Outputs" },
  { id: "agentic-ide", labelKey: "nav5.work_projects", fallbackLabel: "Projects", matchIds: IDE_IDS },
  { id: "board", labelKey: "nav5.work_activity", fallbackLabel: "Activity" },
];

/** AGENTS: the agents, the tools they can reach, where more come from, the CLI seats. */
export const AGENTS_TABS: readonly DestinationTab[] = [
  { id: "agents", labelKey: "nav5.agents_agents", fallbackLabel: "Agents" },
  { id: "armory", labelKey: "nav5.agents_tools", fallbackLabel: "Tools" },
  { id: "marketplace", labelKey: "nav5.agents_browse", fallbackLabel: "Browse more" },
  { id: "clis", labelKey: "nav5.agents_clis", fallbackLabel: "CLIs", matchIds: ["clis", "cli-test-hub"] },
];

export const PRIMARY_DESTINATIONS: readonly PrimaryDestination[] = [
  // The HUD: the chat, the live voice orb and the memory orb on one screen —
  // the front page, first in the list. The old CHAT and ORB destinations and
  // WORK's "Live" tab were three doors to pieces of it; their ids land here.
  // The key stays "chat" so the new-chat "+" and the tour anchor keep their
  // place on the row.
  {
    key: "chat",
    labelKey: "nav5.hud",
    fallbackLabel: "HUD",
    icon: Gauge,
    target: "chats",
    sections: HUD_SECTION_IDS,
  },
  {
    key: "agents",
    labelKey: "nav5.agents",
    fallbackLabel: "Agents",
    icon: AgentsIcon,
    target: "agents",
    sections: ["agents", "armory", "marketplace", "clis", "cli-test-hub"],
    tabs: AGENTS_TABS,
  },
  {
    key: "work",
    labelKey: "nav5.work",
    fallbackLabel: "Work",
    icon: Briefcase,
    target: "visualization",
    sections: ["visualization", ...IDE_IDS, "board"],
    tabs: WORK_TABS,
  },
  {
    key: "settings",
    labelKey: "nav5.settings",
    fallbackLabel: "Settings",
    icon: SettingsIcon,
    // The hub opens on its MODEL page for this id (see store/settingsPage).
    target: "apikeys",
    sections: SETTINGS_HUB_IDS,
    tourAnchor: "settings",
  },
];

/** The destination a section belongs to, or null for an Advanced-only section. */
export function destinationFor(section: SectionId): PrimaryDestination | null {
  return PRIMARY_DESTINATIONS.find((d) => d.sections.includes(section)) ?? null;
}

/** The tab of `tabs` that `section` counts as, or null. */
export function tabFor(tabs: readonly DestinationTab[], section: SectionId): DestinationTab | null {
  return tabs.find((tab) => tab.id === section || tab.matchIds?.includes(section)) ?? null;
}

/**
 * Sections whose primary row IS the section under the same name: listing them
 * again under Advanced would be a second row for the same door. (WORK lands on
 * Artifacts, but its row is named "Work", so Artifacts keeps its own entry.)
 */
const PRIMARY_FRONT_DOORS: readonly SectionId[] = ["chats", "agents"];

/**
 * The sidebar's collapsed "Advanced" group: every sidebar section that is not
 * the front door of a primary destination and not a Settings-hub page.
 *
 * Sections that are ALSO a destination tab (the HUD, Artifacts, Tool armory…)
 * are listed here as well, on purpose: Advanced is the complete index, so a
 * person who learned the old names still finds every one of them by name, and
 * nothing that existed before became unreachable from the sidebar.
 */
export const ADVANCED_NAV_ITEMS: readonly NavItem[] = NAV_GROUPS.flat().filter(
  (item) =>
    !(SETTINGS_HUB_IDS as readonly string[]).includes(item.id) &&
    !PRIMARY_FRONT_DOORS.includes(item.id),
);
