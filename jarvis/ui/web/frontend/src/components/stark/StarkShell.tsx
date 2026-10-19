import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { Brain, Ghost, Globe2, Home, Mic, Orbit, PenSquare, Power, Radar, Search, Settings, Tv, Workflow, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEventStore, type SectionId } from "@/store/events";
import { useQuickSwitcher } from "@/store/quickSwitcher";
import { useAssistantStatus } from "@/hooks/useAssistantStatus";
import { formatAssistantStatus } from "@/lib/assistantStatus";
import { openSettingsDestination } from "@/lib/openSettings";
import { focusChatComposer, startNewTextChat } from "@/lib/newChat";
import { requestVoiceHangup } from "@/lib/voiceApi";
import { readCity } from "@/components/stark/WeatherGlobe";
import { useVoiceToggles } from "@/components/deck/HudControls";
import type { FullMapRequest } from "@/components/deck/HudMemoryPanel";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The full memory map (the memory orb, hand control, the data import), opened
 * over a Stark screen from the rail. Its own chunk: force-graph and the hand
 * tracker stay off the HUD until someone asks for them.
 */
const MemoryOrbView = lazy(() =>
  import("@/views/MemoryOrbView").then((m) => ({ default: m.MemoryOrbView })),
);

export type StarkScreen = "command" | "intelligence" | "orbital" | "world" | "live" | "ghost";

interface RailItem {
  key: string;
  icon: LucideIcon;
  screen?: StarkScreen;
  section?: SectionId;
  action?: "memory" | "voice";
}

/** The rail: the HUD's own screens first, then the places people go from it. */
const RAIL: RailItem[] = [
  { key: "command", icon: Home, screen: "command", section: "deck" },
  { key: "intelligence", icon: Radar, screen: "intelligence", section: "intelligence" },
  { key: "orbital", icon: Orbit, screen: "orbital", section: "orbital" },
  { key: "world", icon: Globe2, screen: "world", section: "world-intelligence" },
  { key: "live", icon: Tv, screen: "live", section: "live-feeds" },
  { key: "ghost", icon: Ghost, screen: "ghost", section: "ghost" },
  { key: "memory", icon: Brain, action: "memory" },
  { key: "agents", icon: Workflow, section: "agents" },
  { key: "voice", icon: Mic, action: "voice" },
  { key: "settings", icon: Settings, section: "settings" },
];

/**
 * The frame every Stark screen shares (Command, Intelligence, Orbital): the
 * rail down the left, the header with the clock, the name and the three
 * tools, and the screen itself. The app's own sidebar steps aside while a
 * Stark screen is up (App.tsx `FULLSCREEN_SECTIONS`), so the rail is how you
 * leave; the search icon reaches every other section.
 */
export function StarkShell({
  screen,
  subtitle,
  aside,
  badge,
  className,
  testId,
  children,
}: {
  screen: StarkScreen;
  /** The line under the name ("// Intelligence"). */
  subtitle?: string;
  /** Extra readouts between the clock and the name (world clocks). */
  aside?: ReactNode;
  /** A small readout under the header tools. */
  badge?: ReactNode;
  className?: string;
  testId?: string;
  children: ReactNode;
}) {
  const [fullMap, setFullMap] = useState<FullMapRequest | null>(null);
  return (
    <div
      className={cn("stark-hud flex h-full min-h-0 flex-col font-hud lg:flex-row", className)}
      data-testid={testId}
      data-screen={screen}
    >
      <StarkRail screen={screen} memoryOpen={fullMap !== null} onMemory={() => setFullMap({ mode: "memory" })} />
      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
        <StarkHeader subtitle={subtitle} aside={aside} badge={badge} />
        <div className="relative flex min-h-0 flex-1 flex-col">{children}</div>
        {fullMap && <FullMemoryMap request={fullMap} onClose={() => setFullMap(null)} />}
      </div>
    </div>
  );
}

function StarkRail({ screen, memoryOpen, onMemory }: { screen: StarkScreen; memoryOpen: boolean; onMemory: () => void }) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const go = (item: RailItem) => {
    if (item.action === "memory") onMemory();
    else if (item.action === "voice") openSettingsDestination("voice");
    else if (item.section) setActiveSection(item.section);
  };
  return (
    <nav
      aria-label={t("stark.rail.label")}
      className="stark-rail flex shrink-0 items-center gap-1 overflow-x-auto px-2 py-1 lg:w-[84px] lg:flex-col lg:overflow-visible lg:px-0 lg:py-4"
      data-testid="stark-rail"
    >
      <span aria-hidden className="hidden h-2 w-2 rotate-45 border border-primary/50 lg:mb-4 lg:block" />
      {RAIL.map((item) => {
        // The memory map opens over the screen; while it is up, it is current.
        const current = memoryOpen ? item.action === "memory" : item.screen === screen;
        const Icon = item.icon;
        return (
          <button
            key={item.key}
            type="button"
            onClick={() => go(item)}
            aria-current={current ? "page" : undefined}
            data-testid={`stark-rail-${item.key}`}
            className={cn(
              "stark-rail-item group relative flex min-w-[64px] flex-col items-center gap-1 px-1 py-1.5 lg:w-full lg:py-2",
              current && "is-current",
            )}
          >
            <span className="stark-rail-ring flex h-10 w-10 items-center justify-center rounded-full">
              <Icon className="h-[18px] w-[18px]" aria-hidden />
            </span>
            <span className="text-[10px] font-semibold uppercase tracking-[0.12em]">
              {t(`stark.rail.${item.key}`)}
            </span>
          </button>
        );
      })}
      <StandbyButton />
    </nav>
  );
}

/**
 * The rail's power button: stand by. It ends a call in progress and mutes
 * the microphone, so nothing is heard until the person presses again — and
 * the press after that unmutes. The app keeps running; quitting stays in the
 * window's own controls.
 */
function StandbyButton() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const voiceState = useEventStore((s) => s.voiceState);
  const { micMuted, micBusy, pressMic } = useVoiceToggles();
  const asleep = micMuted === true;
  const press = async () => {
    if (!asleep && (voiceState === "listening" || voiceState === "thinking" || voiceState === "speaking")) {
      try {
        await requestVoiceHangup();
      } catch (error) {
        pushToast("error", (error as Error).message);
      }
    }
    await pressMic();
  };
  const label = t(asleep ? "stark.rail.wake" : "stark.rail.standby");
  return (
    <button
      type="button"
      onClick={() => void press()}
      disabled={micBusy || micMuted === null}
      aria-pressed={asleep}
      title={label}
      aria-label={label}
      data-testid="stark-rail-standby"
      className={cn(
        "stark-rail-item ml-auto flex min-w-[64px] flex-col items-center gap-1 px-1 py-1.5 disabled:opacity-40 lg:ml-0 lg:mt-auto lg:w-full",
        asleep && "is-asleep",
      )}
    >
      <span className="stark-rail-ring flex h-10 w-10 items-center justify-center rounded-full">
        <Power className="h-[18px] w-[18px]" aria-hidden />
      </span>
      <span className="text-[10px] font-semibold uppercase tracking-[0.12em]">{label}</span>
    </button>
  );
}

function StarkHeader({ subtitle, aside, badge }: { subtitle?: string; aside?: ReactNode; badge?: ReactNode }) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const status = useAssistantStatus();
  const online = status.phase !== "offline" && status.phase !== "booting" && status.phase !== "error";
  const stateLine = status.phase === "ready" ? t("stark.status.online") : formatAssistantStatus(status, t);
  return (
    <header className="relative grid shrink-0 grid-cols-[1fr_auto] items-start gap-x-4 gap-y-2 px-4 pb-1 pt-3 xl:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] xl:px-6">
      <div className="flex min-w-0 items-start gap-6">
        <HeaderClock />
        {aside}
      </div>

      {/* The name in its angular frame, with the state under it. */}
      <div className="col-span-2 row-start-2 flex justify-center xl:col-span-1 xl:col-start-2 xl:row-start-1">
        <div className="stark-title-frame relative flex flex-col items-center px-16 pb-3 pt-1">
          <span aria-hidden className="absolute left-1/2 top-0 h-[2px] w-14 -translate-x-1/2 bg-primary shadow-[0_0_10px_hsl(var(--primary))]" />
          <span className="stark-title mt-2 text-3xl font-semibold tracking-[0.42em] text-foreground xl:text-[2.6rem]" data-testid="stark-wordmark">
            {wordmark(assistantName)}
          </span>
          {subtitle && (
            <span className="mt-0.5 text-sm font-semibold uppercase tracking-[0.34em] text-primary">// {subtitle}</span>
          )}
          <span
            className={cn(
              "mt-1 flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.3em]",
              status.tone === "error" ? "text-destructive" : online ? "text-primary" : "text-[hsl(var(--stark-hot))]",
            )}
            data-testid="stark-state"
            data-phase={status.phase}
            aria-live="polite"
          >
            <span
              aria-hidden
              className={cn(
                "h-2.5 w-2.5 rounded-full",
                online ? "bg-primary shadow-[0_0_8px_hsl(var(--primary))]" : "bg-[hsl(var(--stark-hot))]",
                status.pulse && "animate-pulse motion-reduce:animate-none",
              )}
            />
            {stateLine}
          </span>
          <svg aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 h-6 w-full" preserveAspectRatio="none" viewBox="0 0 400 24">
            <path d="M0 6 H40 L58 22 H342 L360 6 H400" fill="none" stroke="hsl(var(--primary))" strokeWidth={1} opacity={0.6} vectorEffect="non-scaling-stroke" />
          </svg>
        </div>
      </div>

      <div className="col-start-2 row-start-1 flex flex-col items-end gap-2 xl:col-start-3">
        <div className="stark-toolbar flex items-center gap-1 rounded-full px-2 py-1" role="toolbar" aria-label={t("stark.tools.label")}>
          <ToolButton icon={Search} label={t("stark.tools.search")} testId="stark-tool-search" onClick={() => useQuickSwitcher.getState().show()} />
          <ToolButton
            icon={PenSquare}
            label={t("stark.tools.new_chat")}
            testId="stark-tool-new"
            onClick={() => {
              startNewTextChat();
              focusChatComposer();
            }}
          />
          <ToolButton icon={Settings} label={t("stark.tools.settings")} testId="stark-tool-settings" onClick={() => setActiveSection("settings")} />
        </div>
        {badge}
      </div>
    </header>
  );
}

function ToolButton({ icon: Icon, label, onClick, testId }: { icon: LucideIcon; label: string; onClick: () => void; testId: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      data-testid={testId}
      className="flex h-9 w-9 items-center justify-center rounded-full text-foreground/80 transition-colors hover:bg-primary/10 hover:text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
    >
      <Icon className="h-[18px] w-[18px]" aria-hidden />
    </button>
  );
}

/** The big clock: hours and minutes, the date, and where the owner is. */
export function HeaderClock() {
  const now = useNow(1000);
  const city = readCity();
  const place = city
    ? `${city.name}${city.country ? `, ${city.country}` : ""}`
    : Intl.DateTimeFormat().resolvedOptions().timeZone.replace(/_/g, " ");
  return (
    <div className="flex shrink-0 flex-col leading-none" data-testid="stark-clock">
      <span className="text-5xl font-medium tabular-nums tracking-wide text-foreground">
        {now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
      </span>
      <span className="mt-1.5 text-sm font-semibold uppercase tracking-[0.16em] text-foreground/80">
        {now.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "2-digit", year: "numeric" })}
      </span>
      <span className="mt-1 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">{place}</span>
    </div>
  );
}

/** The time, ticking every `ms`. */
export function useNow(ms: number): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}

/** The name as the header's wordmark: "Jarvis" is J.A.R.V.I.S., any other
 * name the owner gave the assistant is set in capitals. */
export function wordmark(name: string): string {
  const clean = name.trim();
  if (clean.toLowerCase() === "jarvis") return "J.A.R.V.I.S.";
  return (clean || "J.A.R.V.I.S.").toUpperCase();
}

/**
 * The full memory map over the screen, closed with the X or Escape. The
 * screen stays mounted underneath, so closing it lands exactly where you were.
 */
function FullMemoryMap({ request, onClose }: { request: FullMapRequest; onClose: () => void }) {
  const t = useT();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("hud3.memory.map_title")}
      data-testid="hud-full-map"
      className="absolute inset-0 z-30 flex flex-col bg-background font-sans"
    >
      <Suspense
        fallback={
          <div role="status" className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            {t("common.loading")}
          </div>
        }
      >
        <MemoryOrbView initialMode={request.mode} importOpen={request.importOpen} />
      </Suspense>
      <button
        type="button"
        onClick={onClose}
        data-testid="hud-full-map-close"
        aria-label={t("hud3.memory.map_close")}
        title={t("hud3.memory.map_close")}
        className="absolute bottom-4 right-4 z-40 flex items-center gap-1.5 border border-primary/50 bg-background/80 px-2.5 py-1.5 font-mono text-micro uppercase tracking-[0.16em] text-primary backdrop-blur transition-colors hover:bg-primary/10"
      >
        <X className="h-3.5 w-3.5" aria-hidden />
        {t("hud3.memory.map_close")}
      </button>
    </div>
  );
}
