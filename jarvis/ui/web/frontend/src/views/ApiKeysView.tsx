import { useEffect, useState } from "react";
import { AudioLines, Brain, ChevronDown, Loader2, RefreshCw, Waypoints } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { LiveProfile } from "@/components/providers/LiveProfile";
import { BrainPicker } from "@/components/providers/BrainPicker";
import {
  makeProviderCategories,
  ProviderCategory,
  useTierHealth,
} from "@/components/providers/ProviderTierSection";
import { RoutePolicyCard } from "@/components/routing/RoutePolicyCard";
import { Button } from "@/components/ui/button";
import { AgentsTab } from "@/views/apikeys/AgentsTab";
import { KeyField } from "@/views/apikeys/KeyField";
import { SettingsGroup } from "@/views/apikeys/settingsUi";
import { useProviders, useSectionHealth } from "@/hooks/useProviders";
import {
  APIKEYS_TAB_EVENT,
  clearApiKeysTabRequest,
  requestedApiKeysTab,
  takeRequestedApiKeysTab,
} from "@/lib/apiKeysTab";
import { pageForApiKeysTab } from "@/store/settingsPage";
import { useProviderFamilies } from "@/lib/providerFamilies";
import { useLocaleChunk, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { JarvisApiGroup } from "@/views/settings/JarvisApiGroup";
import { RealtimeVoiceGroup } from "@/views/settings/RealtimeVoiceGroup";
import { SettingsCard } from "@/views/settings/SettingsLayout";
import { useHermesBrain } from "@/hooks/useHermesBrain";
import { useVoiceMode } from "@/hooks/useVoiceMode";
import { useLocalMode } from "@/lib/localMode";
import { openSettingsDestination } from "@/lib/openSettings";

// Agents first: the providers are set up there; live calls reuse the OpenAI login.
const TABS = ["agents", "voice"] as const;
type Tab = (typeof TABS)[number];
const TAB_STORAGE_KEY = "jarvis.apikeys.tab";

const isTab = (value: string | null): value is Tab => value !== null && (TABS as readonly string[]).includes(value);

function rememberTab(tab: Tab) {
  try {
    localStorage.setItem(TAB_STORAGE_KEY, tab);
  } catch {
    // Remembering the tab is a convenience; without storage it resets.
  }
}

function initialTab(): Tab {
  // A link from elsewhere in the app (requestApiKeysTab) wins over the last tab.
  const asked = takeRequestedApiKeysTab();
  if (isTab(asked)) {
    rememberTab(asked);
    return asked;
  }
  try {
    const saved = localStorage.getItem(TAB_STORAGE_KEY);
    if (saved && (TABS as readonly string[]).includes(saved)) return saved as Tab;
  } catch {
    // Private mode / no storage: open on the first tab.
  }
  return "agents";
}

/**
 * API Keys, in two parts behind one centred switch, both as a provider list
 * beside the selected provider's settings:
 *
 * - Live calls: the live voice runs on OpenAI GPT-Live — paid with the
 *   ChatGPT subscription or one OpenAI key — with its voice and the thinking
 *   model that answers with tools;
 * - Agents: every company the assistant's agents can run on, any number of
 *   them on at once, each reached by its subscription or its API key.
 */
/**
 * The fork's two provider faces on top of the API Keys page (P1-G,
 * 2026-10-11): the Settings hub opens on MODEL and VOICE, the two questions
 * people come with, and the page below — the providers, their sign-ins and
 * keys — lives under Advanced. All three are the `apikeys` section; the face
 * is the hub's choice (store/settingsPage.ts).
 *
 * - `model`: the brain — the one-list `BrainPicker` (Hermes decides / On this
 *   computer / Free cloud), the full brain catalogue folded under it, and the
 *   route policy (hidden while Hermes, which routes itself, is the brain);
 * - `voice`: the engine (Realtime | Pipeline) and either the live-call setup
 *   or the Pipeline's voice output, voice input and wording providers.
 *
 * Omitted, the view is the API Keys page itself.
 */
export type ApiKeysScope = "model" | "voice";

export function ApiKeysView({ scope }: { scope?: ApiKeysScope } = {}) {
  if (scope) return <ProviderFace scope={scope} />;
  return <ApiKeysPage />;
}

function ApiKeysPage() {
  const t = useT();
  // The page's own strings load with it; nothing paints raw keys meanwhile.
  const stringsReady = useLocaleChunk("providers");
  const { providers, refetch } = useProviders();
  const families = useProviderFamilies();
  const { health } = useSectionHealth();
  const [tab, setTab] = useState<Tab>(initialTab);
  const choose = (next: Tab) => {
    setTab(next);
    rememberTab(next);
  };
  // A link that arrives while the page is already open moves it too.
  useEffect(() => {
    const onRequest = () => {
      const asked = takeRequestedApiKeysTab();
      if (isTab(asked)) {
        setTab(asked);
        rememberTab(asked);
      }
    };
    window.addEventListener(APIKEYS_TAB_EVENT, onRequest);
    return () => window.removeEventListener(APIKEYS_TAB_EVENT, onRequest);
  }, []);

  if (!stringsReady) {
    return (
      <div role="status" aria-busy="true" className="flex h-full items-center justify-center">
        <Loader2 aria-hidden="true" className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col" data-tour="apikeys-page">
      <div className="shrink-0 px-8">
        <PageHeader
          title={t("apikeys_view.title")}
          description={t("providers_page.subtitle")}
          actions={
            <Button
              size="sm"
              variant="ghost"
              className="text-muted-foreground"
              data-testid="providers-refresh"
              disabled={families.loading}
              onClick={() => {
                refetch();
                void families.reload();
              }}
            >
              {families.loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              <CheckedAgo at={families.checkedAt} />
            </Button>
          }
        />
        <div className="flex justify-center">
          <TabSwitch value={tab} onChange={choose} />
        </div>
      </div>

      <div
        data-testid="api-keys-provider-scroll"
        className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis px-8 pb-10 pt-6"
      >
        <div key={tab} role="tabpanel" id="apikeys-panel" aria-labelledby={`apikeys-tab-${tab}`} className="profile-rise w-full">
          {tab === "voice" && <VoiceTab data={families} onSaved={refetch} />}
          {tab === "agents" &&
            (families.error === "restart_required" ? (
              <RestartNeeded />
            ) : (
              <AgentsTab data={families} providers={providers} health={health} onProvidersChanged={refetch} />
            ))}
          {/* The Control Key unlocks the browser UI and authenticates local agents. */}
          {tab === "agents" && (
            <div className="mt-10">
              <JarvisApiGroup />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** A segmented switch between the page's parts. */
function TabSwitch({ value, onChange }: { value: Tab; onChange: (tab: Tab) => void }) {
  const t = useT();
  return (
    <div
      role="tablist"
      aria-label={t("apikeys_view.title")}
      data-testid="api-keys-category-tabs"
      className="inline-flex rounded-lg border border-border/60 bg-card/40 p-0.5"
    >
      {TABS.map((tab) => (
        <button
          key={tab}
          type="button"
          role="tab"
          id={`apikeys-tab-${tab}`}
          aria-selected={value === tab}
          aria-controls="apikeys-panel"
          onClick={() => onChange(tab)}
          className={cn(
            "h-7 rounded-md px-3 text-sm font-medium transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            value === tab ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {t(`providers_page.tab_${tab}`)}
        </button>
      ))}
    </div>
  );
}

/**
 * Live calls run on OpenAI GPT-Live, the one live speech-to-speech model the
 * assistant runs — so the tab is that one provider's settings pane: how it is
 * paid for, its voice and the thinking model that answers with tools.
 */
function VoiceTab({ data, onSaved }: { data: ReturnType<typeof useProviderFamilies>; onSaved: () => void }) {
  const openai = data.families?.find((family) => family.id === "openai");
  return (
    <div data-testid="apikeys-voice" className="mx-auto w-full max-w-3xl">
      <SettingsGroup divided={false} className="p-4">
        <LiveProfile
          onSaved={onSaved}
          keyField={
            <KeyField
              slot={openai?.key_slot ?? "openai_api_key"}
              present={openai?.key_present ?? false}
              providerLabel="OpenAI"
              dashboardUrl={openai?.dashboard_url}
              onChanged={() => void data.reload()}
              testId="voice-key"
            />
          }
        />
      </SettingsGroup>
    </div>
  );
}

/** "Checked 2 min ago", re-rendered every half minute. */
function CheckedAgo({ at }: { at: number | null }) {
  const t = useT();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  if (at === null) return <>{t("providers_page.checking")}</>;
  const minutes = Math.floor(Math.max(0, now - at) / 60_000);
  return (
    <>
      {minutes < 1
        ? t("providers_page.checked_now")
        : t("providers_page.checked_minutes").replace("{0}", String(minutes))}
    </>
  );
}

/**
 * The window runs a newer page than its backend: the bundle reloads on its
 * own after an update, the server does not. One sentence and one button,
 * instead of an error that suggests something is broken.
 */
function RestartNeeded() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [restarting, setRestarting] = useState(false);

  async function restart() {
    setRestarting(true);
    try {
      const response = await fetch("/api/settings/restart-app", { method: "POST" });
      if (response.status === 409) {
        pushToast("warning", t("topbar.restart_missions_running"));
        setRestarting(false);
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      // The window closes and relaunches; the button stays busy until then.
    } catch (cause) {
      pushToast("error", (cause as Error).message);
      setRestarting(false);
    }
  }

  return (
    <div
      role="status"
      data-testid="providers-restart-needed"
      className="flex items-center gap-4 rounded-xl border border-border/60 bg-card/40 px-4 py-3"
    >
      <RefreshCw aria-hidden="true" className="h-4 w-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground">{t("providers_page.restart_title")}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">{t("providers_page.restart_desc")}</p>
      </div>
      <Button size="sm" disabled={restarting} onClick={() => void restart()}>
        {restarting && <Loader2 className="animate-spin" />}
        {t("providers_page.restart_action")}
      </Button>
    </div>
  );
}

/**
 * MODEL or VOICE (see `ApiKeysScope`). Each provider tier is shown on exactly
 * one face, so no two controls set the same config.
 */
function ProviderFace({ scope }: { scope: ApiKeysScope }) {
  const t = useT();
  const stringsReady = useLocaleChunk("providers");
  const hermesBrain = useHermesBrain();
  const { providers, loading, error, refetch, setActiveOptimistic } = useProviders();
  const health = useTierHealth(providers);
  const categories = makeProviderCategories(t);
  const families = useProviderFamilies();
  const { mode: liveMode, setMode: setVoiceMode } = useVoiceMode();
  // A per-machine view preference: it hides hosted cards, it never changes
  // what the app runs on (lib/localMode.ts). The notice above a shortened
  // list carries the switch back.
  const { localMode, setLocalMode } = useLocalMode();
  // The Model page leads with the one-list picker; the full provider
  // catalogue (switching the brain itself) stays one click away.
  const [showAllBrains, setShowAllBrains] = useState(false);
  // A request that brought the hub to this face is served by showing it.
  useEffect(() => {
    if (pageForApiKeysTab(requestedApiKeysTab()) === scope) clearApiKeysTabRequest();
  }, [scope]);

  if (!stringsReady) {
    return (
      <div role="status" aria-busy="true" className="flex h-full items-center justify-center">
        <Loader2 aria-hidden="true" className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const category = (tier: "brain" | "tts" | "stt" | "dictation") => (
    <ProviderCategory
      key={tier}
      meta={categories[tier]}
      tier={tier}
      providers={providers}
      loading={loading}
      error={error}
      onChanged={refetch}
      onActivateOptimistic={setActiveOptimistic}
      health={health[tier]}
      localMode={localMode}
      onDisableLocalMode={() => setLocalMode(false)}
      wideGrid
    />
  );

  // With Hermes as the brain the backend forces voice onto the Pipeline
  // (ADR-0042), so the realtime face would be a control with no effect.
  const realtime = !hermesBrain && liveMode === "realtime";

  return (
    <div className="flex h-full min-h-0 flex-col" data-tour="apikeys-page" data-scope={scope}>
      <div className="shrink-0 px-8">
        <PageHeader
          icon={scope === "model" ? <Brain /> : <AudioLines />}
          title={t(scope === "model" ? "settings_pages.model_title" : "settings_pages.voice_title")}
          description={t(scope === "model" ? "settings_pages.model_subtitle" : "settings_pages.voice_subtitle")}
        />
      </div>
      <div
        data-testid="api-keys-provider-scroll"
        className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis px-8 pb-10"
      >
        <div className="profile-rise mx-auto w-full max-w-[1440px] space-y-6">
          {scope === "model" && (
            <>
              <BrainPicker onChanged={refetch} />
              <button
                type="button"
                data-testid="all-brain-providers-toggle"
                aria-expanded={showAllBrains}
                onClick={() => setShowAllBrains((v) => !v)}
                className="inline-flex items-center gap-1.5 text-meta text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", !showAllBrains && "-rotate-90")} aria-hidden />
                {t("brain_picker.all_providers")}
              </button>
              {showAllBrains && category("brain")}
              {/* Hermes picks model, route and fallback itself: the route
                  policy is dead in that mode, so it is not offered. */}
              {!hermesBrain && <RoutePolicyCard providers={providers} />}
            </>
          )}
          {scope === "voice" && (
            <>
              {/* The ONE Realtime | Pipeline switch (Settings › General links
                  here). Hidden with Hermes as the brain: voice always runs
                  the Pipeline then. */}
              {!hermesBrain && (
                <SettingsCard>
                  <RealtimeVoiceGroup />
                </SettingsCard>
              )}
              {realtime ? (
                <>
                  <PipelineHint
                    onUsePipeline={() => {
                      setVoiceMode("pipeline");
                      openSettingsDestination("model", "brain");
                    }}
                  />
                  <VoiceTab data={families} onSaved={refetch} />
                </>
              ) : (
                <>
                  {category("tts")}
                  {category("stt")}
                  {category("dictation")}
                </>
              )}
            </>
          )}
          <ScopeNote kind={scope} hermesBrain={hermesBrain} />
        </div>
      </div>
    </div>
  );
}

/**
 * Hermes, Nous Portal and a local Qwen server have no native live-audio
 * model, so they never appear in the live-call setup; without this line the
 * person was left looking for them there. There is deliberately no stand-in
 * "Hermes" realtime row: the button moves voice to Pipeline and opens the
 * brain picker.
 */
function PipelineHint({ onUsePipeline }: { onUsePipeline: () => void }) {
  const t = useT();
  return (
    <div
      data-testid="realtime-pipeline-hint"
      className="flex flex-wrap items-start gap-3 rounded-surface border border-border bg-card px-4 py-3"
    >
      <Waypoints className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 flex-1 space-y-1">
        <p className="text-sm font-medium text-foreground">{t("apikeys_view.pipeline_hint_title")}</p>
        <p className="text-xs leading-relaxed text-muted-foreground">{t("apikeys_view.pipeline_hint_body")}</p>
      </div>
      <Button variant="outline" size="sm" onClick={onUsePipeline} data-testid="realtime-pipeline-hint-use">
        {t("apikeys_view.pipeline_hint_action")}
      </Button>
    </div>
  );
}

/**
 * The one line under a face that says where the rest went, and — with
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
  lines.push({
    text: t(kind === "model" ? "settings_pages.model_more" : "settings_pages.voice_more"),
    link: { label: t("settings_pages.open_apikeys"), onClick: () => openSettingsDestination("apikeys") },
  });
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
