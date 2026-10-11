import { useEffect, useRef, useState } from "react";
import {
  AudioLines,
  Bot,
  ChevronDown,
  Brain,
  KeyRound,
  Mic,
  Phone,
  Radio,
  SlidersHorizontal,
  Volume2,
  Wand2,
  Waypoints,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { BrainPicker } from "@/components/providers/BrainPicker";
import { JarvisAgentSection } from "@/components/JarvisAgentSection";
import { VoiceProviderSettings } from "@/components/providers/VoiceProviderSettings";
import { RoutePolicyCard } from "@/components/routing/RoutePolicyCard";
import { TelephonyPanel } from "@/views/TelephonyView";
import { WikiProviderCard } from "@/views/settings/WikiProviderCard";
import { JarvisApiGroup } from "@/views/settings/JarvisApiGroup";
import { TeamProxyGroup } from "@/views/settings/TeamProxyGroup";
// The provider-card machinery lives in its own module so the voice section's
// "API Keys" tab renders the very same subtree (scoped to the `stt` tier)
// instead of forking a second implementation.
import {
  CategoryHero,
  EngineModeSwitch,
  LocalModeSwitch,
  makeProviderCategories,
  ProviderCategory,
  useTierHealth,
  type LucideIcon,
  type VoiceEngineMode,
} from "@/components/providers/ProviderTierSection";
import {
  type ProviderTier,
  type SectionHealth,
  useProviders,
} from "@/hooks/useProviders";
import { useVoiceMode } from "@/hooks/useVoiceMode";
import { useLocalMode } from "@/lib/localMode";
import {
  APIKEYS_TAB_EVENT,
  clearApiKeysTabRequest,
  requestApiKeysTab,
  requestedApiKeysTab,
} from "@/lib/apiKeysTab";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useHermesBrain } from "@/hooks/useHermesBrain";
import { openSettingsDestination } from "@/lib/openSettings";

// Voice owns its thinking backend. Text and background work use the Agents selection.
type CategoryKey =
  | Exclude<ProviderTier, "computer-use">
  | "subagents"
  | "routing"
  | "jarvis-key"
  | "advanced";

/**
 * Which face of the provider settings this instance shows (P1-G, 2026-10-11).
 *
 * The Settings hub opens on MODEL and VOICE; everything else on this screen
 * lives under its Advanced group as "API keys & routing". Each tier is shown
 * on exactly ONE face, so no two controls set the same config:
 *
 * - `model`: the brain — provider and model that write chat and voice answers;
 * - `voice`: the engine (Realtime | Pipeline) and either the realtime voice
 *   or the voice output (TTS) and voice input (STT) providers;
 * - `advanced`: wording, route policy, the agents' seat, the control key, and
 *   the optional integrations (team proxy, telephony, wiki curator).
 *
 * Omitted, the view keeps its original all-in-one tab set.
 */
export type ApiKeysScope = "model" | "voice" | "advanced";

/** The tabs a scoped face shows, per engine mode. */
export function scopedTabs(
  scope: ApiKeysScope,
  engineMode: VoiceEngineMode,
  hermesBrain: boolean,
): CategoryKey[] {
  switch (scope) {
    case "model":
      return ["brain"];
    case "voice":
      // With Hermes as the brain the backend forces voice onto the Pipeline
      // (ADR-0042), so the realtime face would be a control with no effect.
      return engineMode === "realtime" && !hermesBrain ? ["realtime"] : ["tts", "stt"];
    case "advanced":
      // Hermes picks model, route and fallback itself: the route policy is
      // dead in that mode, so its tab is not offered.
      return hermesBrain
        ? ["dictation", "subagents", "jarvis-key", "advanced"]
        : ["dictation", "routing", "subagents", "jarvis-key", "advanced"];
  }
}

const PIPELINE_TABS: CategoryKey[] = [
  "brain",
  "tts",
  "stt",
  "dictation",
  "subagents",
  "jarvis-key",
  "advanced",
];
const REALTIME_TABS: CategoryKey[] = [
  "realtime",
  "subagents",
  "jarvis-key",
  "advanced",
];

/** The one engine whose tab set holds `tab`, or null when both (or neither) do. */
function engineOwningTab(tab: CategoryKey): VoiceEngineMode | null {
  const pipeline = PIPELINE_TABS.includes(tab);
  const realtime = REALTIME_TABS.includes(tab);
  if (pipeline === realtime) return null;
  return pipeline ? "pipeline" : "realtime";
}

export function ApiKeysView({ scope }: { scope?: ApiKeysScope } = {}) {
  const t = useT();
  const hermesBrain = useHermesBrain();
  // The Model page leads with the one-list picker; the full provider
  // catalogue (switching the brain itself) stays one click away.
  const [showAllBrains, setShowAllBrains] = useState(false);
  const { providers, loading, error, refetch, setActiveOptimistic } =
    useProviders();
  // Per-tab health (amber = the active provider isn't set up, red = it's set up
  // but failing a live check). Best-effort and off the render-blocking path.
  const health = useTierHealth(providers);
  const categories = makeProviderCategories(t);
  const [active, setActive] = useState<CategoryKey>("brain");
  const [engineMode, setEngineMode] = useState<VoiceEngineMode>("pipeline");
  // The LIVE `[voice].mode` (+ cross-family availability) for the "Active"
  // badge AND for gating the segment's own persistence (Feature A). See
  // VoiceEngineMode / EngineModeSwitch in the provider module.
  const {
    mode: liveMode,
    realtimeAvailable,
    setMode: setVoiceMode,
    isLoading: liveModeLoading,
  } = useVoiceMode();
  // Show only providers that run on the user's own hardware. A per-machine view
  // preference (localStorage), deliberately NOT a config switch: it hides cards,
  // it never changes what the app runs on. See lib/localMode.ts.
  const { localMode, setLocalMode } = useLocalMode();

  // Reset the selected tab to the mode's first tab whenever the mode changes,
  // so switching Pipeline→Realtime never leaves `active` pointing at a tab
  // that no longer exists in the new mode (e.g. "tts").
  // A tab another part of the app asked for (the first-run guide) wins over
  // the mode's first tab while it is still in force.
  const tabsFor = (mode: VoiceEngineMode): CategoryKey[] =>
    scope
      ? scopedTabs(scope, mode, hermesBrain)
      : mode === "realtime"
        ? REALTIME_TABS
        : PIPELINE_TABS;

  useEffect(() => {
    const tabs = tabsFor(engineMode);
    const wanted = requestedApiKeysTab() as CategoryKey | null;
    setActive(wanted && tabs.includes(wanted) ? wanted : tabs[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- tabsFor reads only scope + hermesBrain
  }, [engineMode, scope, hermesBrain]);

  useEffect(() => {
    const onRequest = (event: Event) => {
      const wanted = (event as CustomEvent<string | null>).detail as CategoryKey | null;
      // A tab only the other engine has (the brain while Realtime is shown):
      // show that engine's tabs; the mode effect above then lands on it.
      const owner = wanted ? engineOwningTab(wanted) : null;
      if (owner && owner !== engineMode && scope !== "model" && scope !== "advanced") {
        setEngineMode(owner);
        return;
      }
      const tabs = tabsFor(engineMode);
      setActive(wanted && tabs.includes(wanted) ? wanted : tabs[0]);
    };
    window.addEventListener(APIKEYS_TAB_EVENT, onRequest);
    return () => window.removeEventListener(APIKEYS_TAB_EVENT, onRequest);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- tabsFor reads only scope + hermesBrain
  }, [engineMode, scope, hermesBrain]);

  const selectTab = (key: CategoryKey) => {
    clearApiKeysTabRequest();
    setActive(key);
  };

  // Open the view on the engine that is actually LIVE (once, when the mode
  // query resolves) — a user whose voice runs on Realtime should not land on
  // the Pipeline tab set. Later live-mode changes never yank the view.
  const viewSyncedToLive = useRef(false);
  useEffect(() => {
    if (viewSyncedToLive.current || liveModeLoading) return;
    viewSyncedToLive.current = true;
    const wanted = requestedApiKeysTab() as CategoryKey | null;
    const owner = wanted ? engineOwningTab(wanted) : null;
    setEngineMode(owner ?? (liveMode === "realtime" ? "realtime" : "pipeline"));
  }, [liveMode, liveModeLoading]);

  const modeTabs = tabsFor(engineMode);
  // The engine switch belongs to the VOICE face only (it used to sit on the
  // all-in-one page AND as a Realtime toggle in Settings › General). With
  // Hermes as the brain voice always runs the Pipeline, so it is hidden there.
  const showEngineSwitch = (scope === undefined || scope === "voice") && !(scope === "voice" && hermesBrain);
  const header = scope === "model"
    ? { icon: <Brain className="h-4 w-4 text-muted-foreground" />, title: t("settings_pages.model_title"), subtitle: t("settings_pages.model_subtitle") }
    : scope === "voice"
      ? { icon: <AudioLines className="h-4 w-4 text-muted-foreground" />, title: t("settings_pages.voice_title"), subtitle: t("settings_pages.voice_subtitle") }
      : scope === "advanced"
        ? { icon: <KeyRound className="h-4 w-4 text-muted-foreground" />, title: t("settings_pages.apikeys_title"), subtitle: t("settings_pages.apikeys_subtitle") }
        : { icon: <KeyRound className="h-4 w-4 text-muted-foreground" />, title: t("apikeys_view.title"), subtitle: t("apikeys_view.subtitle") };

  return (
    <div className="flex h-full min-h-0 flex-col" data-tour="apikeys-page" data-scope={scope ?? "all"}>
      <ViewHeader
        icon={header.icon}
        title={header.title}
        subtitle={header.subtitle}
        right={
          <div className="flex items-center gap-3">
            {scope !== "advanced" && (
              <LocalModeSwitch enabled={localMode} onToggle={setLocalMode} />
            )}
            {showEngineSwitch && (
              <EngineModeSwitch
                mode={engineMode}
                liveMode={liveMode}
                realtimeAvailable={realtimeAvailable}
                onSelect={setEngineMode}
                onSetVoiceMode={setVoiceMode}
              />
            )}
          </div>
        }
      />

      {/* A face with one tab needs no tab bar: MODEL is the brain, nothing else. */}
      {modeTabs.length > 1 && (
        <CategoryTabs
          active={active}
          onSelect={selectTab}
          health={health}
          tabs={modeTabs}
        />
      )}

      <div
        data-testid="api-keys-provider-scroll"
        className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis px-6 py-3"
      >
        {/* Use the available desktop width while keeping forms at a readable
            measure. The key re-runs the rise animation on tab/mode changes. */}
        <div
          key={`${engineMode}-${active}`}
          className="profile-rise mx-auto w-full max-w-[1440px]"
        >
          {scope === "model" && active === "brain" && <BrainPicker onChanged={refetch} />}
          {scope === "model" && active === "brain" && (
            <button
              type="button"
              data-testid="all-brain-providers-toggle"
              aria-expanded={showAllBrains}
              onClick={() => setShowAllBrains((v) => !v)}
              className="mb-3 inline-flex items-center gap-1.5 text-meta text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", !showAllBrains && "-rotate-90")} aria-hidden />
              {t("brain_picker.all_providers")}
            </button>
          )}
          {(active === "brain" ||
            active === "tts" ||
            active === "stt" ||
            active === "dictation") && !(scope === "model" && active === "brain" && !showAllBrains) && (
            <ProviderCategory
              meta={categories[active]}
              tier={active}
              providers={providers}
              loading={loading}
              error={error}
              onChanged={refetch}
              onActivateOptimistic={setActiveOptimistic}
              health={health[active]}
              localMode={localMode}
              onDisableLocalMode={() => setLocalMode(false)}
              wideGrid
            />
          )}
          {/* Scoped, the route policy is its own Advanced tab ("routing"). */}
          {active === "brain" && scope === undefined && <RoutePolicyCard providers={providers} />}
          {active === "routing" && <RoutePolicyCard providers={providers} />}
          {scope === "model" && <ScopeNote kind="model" hermesBrain={hermesBrain} />}
          {scope === "voice" && <ScopeNote kind="voice" hermesBrain={hermesBrain} />}
          {scope === "advanced" && hermesBrain && <ScopeNote kind="advanced" hermesBrain />}
          {active === "realtime" && (
            <VoiceProviderSettings
              providers={providers}
              loading={loading}
              error={error}
              onChanged={refetch}
              onActivateOptimistic={setActiveOptimistic}
              health={health.realtime}
              localMode={localMode}
              onDisableLocalMode={() => setLocalMode(false)}
              onUsePipeline={() => {
                setVoiceMode("pipeline");
                if (scope) openSettingsDestination("model", "brain");
                else requestApiKeysTab("brain");
              }}
            />
          )}
          {active === "subagents" && <SubagentCategory />}
          {active === "jarvis-key" && <JarvisKeyCategory />}
          {active === "advanced" && <AdvancedCategory />}
        </div>
      </div>
    </div>
  );
}

/**
 * The segmented category navigation. The four core categories are grouped in one
 * pill container; the de-emphasized "Advanced" tab is set apart by a divider and
 * neutral (non-gold) styling so it reads as secondary, never competing with the
 * four primary categories.
 */
function CategoryTabs({
  active,
  onSelect,
  health,
  tabs,
}: {
  active: CategoryKey;
  onSelect: (key: CategoryKey) => void;
  /** Per-tab health rollup keyed by category; absent keys render no dot. */
  health: Record<string, SectionHealth>;
  /** The mode-derived tab list (PIPELINE_TABS / REALTIME_TABS) — "advanced",
   *  if present, is rendered separately (de-emphasized, past a divider). */
  tabs: CategoryKey[];
}) {
  const t = useT();
  type CoreTab = Exclude<CategoryKey, "advanced" | "jarvis-key">;
  const tabMeta: Record<CoreTab, { label: string; icon: LucideIcon }> = {
    brain: { label: t("apikeys_view.tab_brain"), icon: Brain },
    tts: { label: t("apikeys_view.tab_tts"), icon: Volume2 },
    stt: { label: t("apikeys_view.tab_stt"), icon: Mic },
    realtime: { label: t("apikeys_view.tab_realtime"), icon: Radio },
    dictation: { label: t("apikeys_view.tab_dictation"), icon: Wand2 },
    subagents: { label: t("apikeys_view.tab_subagents"), icon: Bot },
    routing: { label: t("settings_pages.tab_routing"), icon: Waypoints },
  };
  const coreTabs = tabs.filter(
    (key): key is CoreTab => key !== "advanced" && key !== "jarvis-key",
  );
  const showJarvisKey = tabs.includes("jarvis-key");
  const showAdvanced = tabs.includes("advanced");
  return (
    <div
      data-testid="api-keys-category-tabs"
      className="shrink-0 overflow-x-auto border-b border-border px-6 scrollbar-jarvis"
    >
      {/* Underline tabs on the header's own rule — no pill group inside a
          frame inside a bar. The core tabs and the two secondary ones share
          one baseline; a hairline separates them. */}
      <div
        role="tablist"
        className="flex min-w-max flex-nowrap items-center gap-1"
      >
        {coreTabs.map((key) => (
          <TabButton
            key={key}
            icon={tabMeta[key].icon}
            label={tabMeta[key].label}
            selected={active === key}
            onClick={() => onSelect(key)}
            health={health[key]}
          />
        ))}
        {(showJarvisKey || showAdvanced) && (
          <span
            className="mx-2 hidden h-4 w-px bg-border sm:block"
            aria-hidden="true"
          />
        )}
        {showJarvisKey && (
          <TabButton
            icon={KeyRound}
            label={t("apikeys_view.tab_jarvis_key")}
            selected={active === "jarvis-key"}
            onClick={() => onSelect("jarvis-key")}
            health={health["jarvis-key"]}
          />
        )}
        {showAdvanced && (
          <TabButton
            icon={SlidersHorizontal}
            label={t("apikeys_view.tab_advanced")}
            selected={active === "advanced"}
            onClick={() => onSelect("advanced")}
            health={health.advanced}
          />
        )}
      </div>
    </div>
  );
}

function TabButton({
  icon: Icon,
  label,
  selected,
  onClick,
  health,
}: {
  icon: LucideIcon;
  label: string;
  selected: boolean;
  onClick: () => void;
  /** Optional health rollup driving the corner status dot. */
  health?: SectionHealth;
}) {
  const t = useT();
  // Only the two "needs attention" states draw a dot — amber for "still has to be
  // set up", red for "set up but not working". `ok` / `unknown` stay silent so the
  // tab bar is calm and a dot always means "look here".
  const indicator =
    health?.status === "error"
      ? "error"
      : health?.status === "needs_setup"
        ? "needs_setup"
        : null;
  const statusLabel =
    indicator === "error"
      ? t("apikeys_view.health_error")
      : indicator === "needs_setup"
        ? t("apikeys_view.health_needs_setup")
        : "";
  // Tooltip: the plain-language status plus the backend's one-line detail
  // (e.g. "Groq STT: key invalid"), so hovering explains exactly what's wrong.
  const title = indicator
    ? [statusLabel, health?.detail].filter(Boolean).join(" — ")
    : undefined;

  return (
    <button
      type="button"
      role="tab"
      aria-selected={selected}
      onClick={onClick}
      title={title}
      className={cn(
        "relative -mb-px inline-flex h-10 shrink-0 items-center gap-2 whitespace-nowrap border-b-2 px-3 text-meta font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
        // The underline is an ACTIVE INDICATOR, which is one of the four jobs
        // --primary is a fill for; the selected label rises to the ink ceiling
        // beside it, so "you are here" is stated twice and neither statement
        // is a colour the eye has to decode.
        selected
          ? "border-primary text-foreground-strong"
          : "border-transparent text-muted-foreground hover:text-foreground",
      )}
    >
      {/* The attention dot sits where the icon would, and it is the only hue
          in the bar: fault red for "set up but broken", degraded amber for
          "still to set up". "needs setup" used to be painted in --foreground,
          which made a status the brightest mark on the bar and told the eye
          nothing — a state is never ink. */}
      {indicator ? (
        <span
          aria-hidden="true"
          className={cn(
            "h-2 w-2 shrink-0 rounded-full",
            indicator === "error" ? "bg-destructive" : "bg-warning",
          )}
        />
      ) : (
        <Icon className="h-4 w-4 opacity-80" />
      )}
      {label}
      {indicator && <span className="sr-only">{` (${statusLabel})`}</span>}
    </button>
  );
}

/**
 * The Subagents category — the heavy-task worker selection. `SubagentSection`
 * owns its own data source (/api/jarvis-agent/status) and card system; the hero band
 * just frames it consistently with the provider tiers.
 */
function SubagentCategory() {
  const t = useT();
  return (
    <div role="tabpanel">
      <CategoryHero
        icon={Bot}
        title={t("apikeys_view.cat_subagents_title")}
        description={t("apikeys_view.cat_subagents_desc")}
      />
      <JarvisAgentSection hideHeader />
    </div>
  );
}

/**
 * The dedicated "<Name> Key" category — the per-install Control Key that
 * unlocks the browser UI and authenticates local agents. The tab and hero are
 * named after the configured wake word via the i18n `{name}` token ("Hannes"
 * -> "Hannes Key"), so the section the lock screen points at carries the name
 * the user actually knows their assistant by.
 */
function JarvisKeyCategory() {
  const t = useT();
  return (
    <div role="tabpanel">
      <CategoryHero
        icon={KeyRound}
        title={t("apikeys_view.jarvis_key_title")}
        description={t("apikeys_view.jarvis_key_desc")}
      />
      <div className="space-y-4">
        <JarvisApiGroup />
      </div>
    </div>
  );
}

/**
 * The de-emphasized "Advanced" category — everything that is NOT one of the four
 * core provider categories: the team key proxy, telephony, and the
 * knowledge-Wiki provider. Each block keeps its own labelled sub-section
 * header, so the zone reads as a clearly separated list of optional
 * integrations rather than competing with the four primary categories.
 */
function AdvancedCategory() {
  const t = useT();
  return (
    <div role="tabpanel">
      <CategoryHero
        icon={SlidersHorizontal}
        title={t("apikeys_view.advanced_title")}
        description={t("apikeys_view.advanced_desc")}
      />
      <div className="space-y-4">
        {/* Team key proxy — credential / key-routing management, so it lives
            with the provider keys rather than in the behaviour-focused
            Settings view. */}
        <TeamProxyGroup />
        {/* Telephony — the former standalone screen, embedded as a section (own
            data source /api/telephony/*). */}
        <TelephonySection />
        {/* Wiki — dedicated long-term-memory curator provider/model. Own data
            source (/api/settings/wiki-provider). */}
        <WikiProviderCard />
        {/* Nominative-use trademark notice: provider/integration names and logos
            belong to their owners and are shown only to identify what you connect
            to. Backs the third-party logos used on plugin cards (see
            TRADEMARK.md). */}
        <p className="pt-2 text-micro text-muted-foreground">
          {t("apikeys_view.trademark_notice")}
        </p>
      </div>
    </div>
  );
}

/**
 * Telephony tier section. Visually a sibling of the brain/tts/stt/subagent
 * tiers: the same uppercase tier header (Phone icon + label) above the embedded
 * `TelephonyPanel`, which carries the status / credentials / calls cards in the
 * shared `card-outline` style. The heavier setup scripts + guide moved to the
 * dedicated TelephonySetupView (reached via the panel's "Setup script" button)
 * to keep this section compact. Its own data source (`/api/telephony/*`) is
 * owned by the panel, so this stays a thin wrapper.
 */
function TelephonySection() {
  const t = useT();
  return (
    <section>
      <h3 className="mb-3 inline-flex items-center gap-2 text-micro text-muted-foreground">
        <Phone className="h-3.5 w-3.5" /> {t("apikeys_view.tier_telephony")}
      </h3>
      <TelephonyPanel />
    </section>
  );
}

/**
 * The one line under a scoped face that says where the rest went, and — with
 * Hermes as the brain — which controls are hidden and why. A hidden control
 * is said out loud; a control that silently does nothing is the defect this
 * replaces.
 */
function ScopeNote({ kind, hermesBrain }: { kind: ApiKeysScope; hermesBrain: boolean }) {
  const t = useT();
  const lines: { text: string; link?: { label: string; onClick: () => void } }[] = [];
  if (hermesBrain) {
    lines.push({ text: t(`settings_pages.hermes_note_${kind}`) });
  }
  if (kind === "model") {
    lines.push({
      text: t("settings_pages.model_more"),
      link: { label: t("settings_pages.open_apikeys"), onClick: () => openSettingsDestination("apikeys") },
    });
  }
  if (kind === "voice") {
    lines.push({
      text: t("settings_pages.voice_more"),
      link: { label: t("settings_pages.open_apikeys"), onClick: () => openSettingsDestination("apikeys", "dictation") },
    });
  }
  return (
    <div data-testid={`scope-note-${kind}`} className="mt-6 space-y-1.5 border-t border-border pt-4">
      {lines.map((line) => (
        <p key={line.text} className="text-meta text-muted-foreground">
          {line.text}
          {line.link && (
            <button
              type="button"
              onClick={line.link.onClick}
              className="ml-1.5 font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {line.link.label}
            </button>
          )}
        </p>
      ))}
    </div>
  );
}
