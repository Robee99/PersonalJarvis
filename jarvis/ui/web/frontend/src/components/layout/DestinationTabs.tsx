import { TabBar } from "@/components/layout/SectionTabBar";
import {
  destinationFor,
  tabFor,
} from "@/components/layout/navGroups";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

/**
 * One tab bar over a destination that spans several views — WORK (Outputs ·
 * Projects · Live · Activity · Runs), AGENTS (Agents · Tools · Browse more ·
 * CLIs) and ORB (Map · Notes).
 *
 * Each tab is an existing section id, so the active section IS the tab state:
 * deep links, voice navigation and the back/forward history keep working with
 * no extra routing (the same idiom as `SectionTabBar`). Renders nothing on a
 * section that belongs to no multi-view destination.
 */
export function DestinationTabs() {
  const t = useT();
  const active = useEventStore((s) => s.activeSection);
  const setActive = useEventStore((s) => s.setActiveSection);
  const destination = destinationFor(active);
  const tabs = destination?.tabs;
  if (!destination || !tabs) return null;
  const current = tabFor(tabs, active);
  if (!current) return null;

  const label = (key: string, fallback: string) => {
    const resolved = t(key);
    return resolved === key ? fallback : resolved;
  };

  return (
    <nav
      aria-label={label(destination.labelKey, destination.fallbackLabel)}
      data-testid={`destination-tabs-${destination.key}`}
      className="shrink-0 px-4"
    >
      <TabBar
        tabs={tabs.map((tab) => ({ id: tab.id, label: label(tab.labelKey, tab.fallbackLabel) }))}
        active={current.id}
        onChange={(id) => {
          const tab = tabs.find((candidate) => candidate.id === id);
          if (tab) setActive(tab.id);
        }}
      />
    </nav>
  );
}
