import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bot, Brain, Code2, Coins, Globe2, Plug, Settings2, Sparkles } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { fetchVitals, type Vitals } from "@/components/deck/DeckSignalCards";
import { RingGauge, StarkLamp, StarkPanel } from "@/components/stark/StarkPanel";
import { useEventStore, type SectionId } from "@/store/events";
import { useDeckStore } from "@/store/deck";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * 01 — SYSTEM STATUS: the things that have to be true for a voice turn, each
 * as a lamp and a word. Every row reads the event store; nothing is assumed.
 */
export function SystemStatusPanel({
  brainLabel,
  modelLabel,
  className,
}: {
  brainLabel: string;
  modelLabel: string;
  className?: string;
}) {
  const t = useT();
  const connected = useEventStore((s) => s.connected);
  const voiceReady = useEventStore((s) => s.voiceReady);
  const voiceState = useEventStore((s) => s.voiceState);
  const cuActive = useDeckStore((s) => s.cu.active);
  const micMuted = useDeckStore((s) => s.micMuted);
  const words = useDeckStore((s) => s.wordsSession);
  const setActiveSection = useEventStore((s) => s.setActiveSection);

  const rows: Array<{ key: string; label: string; value: string; lamp: "on" | "warn" | "off"; onClick?: () => void }> = [
    {
      key: "core",
      label: t("stark.status.core"),
      value: t(connected ? "stark.status.online" : "stark.status.offline"),
      lamp: connected ? "on" : "warn",
    },
    {
      key: "voice",
      label: t("stark.status.voice"),
      value: !voiceReady
        ? t("stark.status.starting")
        : micMuted
          ? t("stark.status.muted")
          : t(`voice_state.${voiceState}`),
      lamp: !voiceReady ? "warn" : micMuted ? "off" : "on",
    },
    {
      key: "brain",
      label: t("stark.status.brain"),
      value: brainLabel,
      lamp: brainLabel && brainLabel !== "—" ? "on" : "warn",
      onClick: () => setActiveSection("apikeys"),
    },
    {
      key: "model",
      label: t("stark.status.model"),
      value: modelLabel || "—",
      lamp: modelLabel ? "on" : "off",
      onClick: () => setActiveSection("apikeys"),
    },
    {
      key: "control",
      label: t("stark.status.control"),
      value: t(cuActive ? "stark.status.active" : "stark.status.standby"),
      lamp: cuActive ? "on" : "off",
    },
    {
      key: "words",
      label: t("stark.status.words"),
      value: String(words),
      lamp: words > 0 ? "on" : "off",
    },
  ];

  return (
    <StarkPanel index="01" title={t("stark.status.title")} className={className} testId="stark-status">
      <ul className="space-y-1">
        {rows.map((row) => {
          const body = (
            <>
              <StarkLamp state={row.lamp} />
              <span className="shrink-0 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                {row.label}
              </span>
              <span aria-hidden className="mx-1 h-px flex-1 border-b border-dotted border-primary/20" />
              <span
                className={cn(
                  "min-w-0 max-w-[60%] truncate font-mono text-xs uppercase tracking-wider",
                  row.lamp === "warn" ? "text-[hsl(var(--stark-hot))]" : "text-foreground",
                )}
                title={row.value}
              >
                {row.value}
              </span>
            </>
          );
          return (
            <li key={row.key} data-testid={`stark-status-${row.key}`}>
              {row.onClick ? (
                <button
                  type="button"
                  onClick={row.onClick}
                  className="flex w-full items-center gap-2 text-left transition-colors hover:[&>span:last-child]:text-primary"
                >
                  {body}
                </button>
              ) : (
                <div className="flex items-center gap-2">{body}</div>
              )}
            </li>
          );
        })}
      </ul>
    </StarkPanel>
  );
}

/**
 * 02 — VITALS: CPU, GPU, RAM and VRAM as rings, read from
 * `/api/deck/vitals` every five seconds. A sensor that did not answer draws an
 * empty ring that reads "—", never a zero.
 */
export function VitalsPanel({ className }: { className?: string }) {
  const t = useT();
  const q = useQuery<Vitals>({
    queryKey: ["deck", "vitals"],
    queryFn: fetchVitals,
    refetchInterval: 5_000,
    retry: false,
  });
  const v = q.data;
  const gpu = v?.gpus[0];
  const vram =
    gpu && gpu.vram_used_mb !== null && gpu.vram_total_mb ? gpu.vram_used_mb / gpu.vram_total_mb : null;
  const pct = (n: number | null | undefined) => (n == null ? "—" : `${Math.round(n)}%`);
  return (
    <StarkPanel
      index="02"
      title={t("stark.vitals.title")}
      meta={gpu ? gpu.name.replace(/^NVIDIA (GeForce )?/, "") : undefined}
      className={className}
      testId="stark-vitals"
    >
      {!v ? (
        <p className="text-xs text-muted-foreground">
          {t(q.isError ? "deck.vitals_error" : "deck.vitals_loading")}
        </p>
      ) : (
        <div className="grid grid-cols-4 gap-1">
          <RingGauge value={v.cpu_percent / 100} label="CPU" readout={pct(v.cpu_percent)} size={74} testId="stark-ring-cpu" />
          <RingGauge
            value={gpu?.util_percent != null ? gpu.util_percent / 100 : null}
            label="GPU"
            readout={pct(gpu?.util_percent)}
            sub={gpu?.temp_c != null ? `${Math.round(gpu.temp_c)}°C` : undefined}
            size={74}
            testId="stark-ring-gpu"
          />
          <RingGauge
            value={v.ram_total_gb > 0 ? v.ram_used_gb / v.ram_total_gb : null}
            label="RAM"
            readout={`${v.ram_used_gb.toFixed(1)}`}
            sub={`/${Math.round(v.ram_total_gb)} GB`}
            size={74}
            testId="stark-ring-ram"
          />
          <RingGauge
            value={vram}
            label="VRAM"
            readout={gpu?.vram_used_mb != null ? (gpu.vram_used_mb / 1024).toFixed(1) : "—"}
            sub={gpu?.vram_total_mb ? `/${Math.round(gpu.vram_total_mb / 1024)} GB` : undefined}
            size={74}
            hotAt={0.95}
            testId="stark-ring-vram"
          />
        </div>
      )}
    </StarkPanel>
  );
}

const QUICK: Array<{ id: SectionId; icon: LucideIcon; key: string }> = [
  { id: "agentic-ide", icon: Code2, key: "code" },
  { id: "memory", icon: Brain, key: "memory" },
  { id: "world-intelligence", icon: Globe2, key: "world" },
  { id: "agents", icon: Bot, key: "agents" },
  { id: "skills", icon: Sparkles, key: "skills" },
  { id: "mcps", icon: Plug, key: "tools" },
  { id: "costs", icon: Coins, key: "costs" },
  { id: "settings", icon: Settings2, key: "settings" },
];

/** QUICK ACCESS: the sections people open most, one press away. */
export function QuickAccess({ className }: { className?: string }) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  return (
    <div className={cn("grid grid-cols-4 gap-2 sm:grid-cols-8", className)} data-testid="stark-quick">
      {QUICK.map(({ id, icon: Icon, key }) => (
        <button
          key={id}
          type="button"
          onClick={() => setActiveSection(id)}
          className="stark-tile group flex flex-col items-center gap-1 px-1 py-2"
          data-testid={`stark-quick-${key}`}
        >
          <Icon className="h-4 w-4 text-primary transition-transform group-hover:scale-110" aria-hidden />
          <span className="font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground group-hover:text-foreground">
            {t(`stark.quick.${key}`)}
          </span>
        </button>
      ))}
    </div>
  );
}

/** The header clock: local time to the second and the date, in the HUD's type. */
export function StarkClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const time = now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const date = now.toLocaleDateString(undefined, { weekday: "short", day: "2-digit", month: "short", year: "numeric" });
  return (
    <div className="flex flex-col items-end leading-none" data-testid="stark-clock">
      <span className="font-mono text-lg tabular-nums text-foreground">{time}</span>
      <span className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">{date}</span>
    </div>
  );
}
