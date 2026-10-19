import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bot,
  Brain,
  Code2,
  Coins,
  Cpu,
  FolderOpen,
  Globe2,
  LayoutGrid,
  MonitorSmartphone,
  Mic,
  Sparkles,
  Type,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { fetchVitals, type Vitals } from "@/components/deck/DeckSignalCards";
import { RingGauge, StarkPanel } from "@/components/stark/StarkPanel";
import { fmtK, METER_MAX, useDeckContext } from "@/components/stark/ContextMeter";
import { ContextControls } from "@/components/stark/ContextControls";
import { useEventStore, type SectionId } from "@/store/events";
import { useDeckStore } from "@/store/deck";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * SYSTEM STATUS: the things that have to be true for a voice turn, each as a
 * name, a lamp and a word. Every row reads the event store; nothing is assumed.
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
  const hasBrain = Boolean(brainLabel && brainLabel !== "—");

  const rows: Array<{
    key: string;
    icon: LucideIcon;
    label: string;
    value: string;
    title?: string;
    lamp: "on" | "warn" | "off";
    onClick?: () => void;
  }> = [
    {
      key: "core",
      icon: Cpu,
      label: t("stark.status.core"),
      value: t(connected ? "stark.status.online" : "stark.status.offline"),
      lamp: connected ? "on" : "warn",
    },
    {
      key: "brain",
      icon: Brain,
      label: t("stark.status.models"),
      value: hasBrain ? t("stark.status.ready") : t("stark.status.not_set"),
      title: [brainLabel, modelLabel].filter(Boolean).join(" · "),
      lamp: hasBrain ? "on" : "warn",
      onClick: () => setActiveSection("apikeys"),
    },
    {
      key: "voice",
      icon: Mic,
      label: t("stark.status.voice"),
      value: !voiceReady
        ? t("stark.status.starting")
        : micMuted
          ? t("stark.status.muted")
          : t(`voice_state.${voiceState}`),
      lamp: !voiceReady ? "warn" : micMuted ? "off" : "on",
    },
    {
      key: "control",
      icon: MonitorSmartphone,
      label: t("stark.status.control"),
      value: t(cuActive ? "stark.status.active" : "stark.status.standby"),
      lamp: cuActive ? "on" : "off",
    },
    {
      key: "words",
      icon: Type,
      label: t("stark.status.words"),
      value: String(words),
      lamp: words > 0 ? "on" : "off",
    },
  ];

  return (
    <StarkPanel title={t("stark.status.title")} className={className} testId="stark-status">
      <ul className="space-y-2 pt-1">
        {rows.map((row) => {
          const Icon = row.icon;
          const body = (
            <>
              <span className="stark-icon-ring flex h-6 w-6 shrink-0 items-center justify-center rounded-full">
                <Icon className="h-3 w-3 text-primary" aria-hidden />
              </span>
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground/85">{row.label}</span>
              <span className="flex w-[44%] min-w-0 items-center gap-2">
                <StatusDot state={row.lamp} />
                <span
                  className={cn(
                    "min-w-0 truncate text-sm font-semibold",
                    row.lamp === "warn" ? "text-[hsl(var(--stark-hot))]" : row.lamp === "on" ? "text-primary" : "text-muted-foreground",
                  )}
                  title={row.title ?? row.value}
                >
                  {row.value}
                </span>
              </span>
            </>
          );
          return (
            <li key={row.key} data-testid={`stark-status-${row.key}`}>
              {row.onClick ? (
                <button
                  type="button"
                  onClick={row.onClick}
                  className="flex w-full items-center gap-3 text-left transition-colors hover:[&>span:nth-child(2)]:text-primary"
                >
                  {body}
                </button>
              ) : (
                <div className="flex items-center gap-3">{body}</div>
              )}
            </li>
          );
        })}
      </ul>
    </StarkPanel>
  );
}

function StatusDot({ state }: { state: "on" | "warn" | "off" }) {
  return (
    <span
      aria-hidden
      className={cn(
        "h-2 w-2 shrink-0 rounded-full",
        state === "on" && "bg-[hsl(var(--stark-ok))] shadow-[0_0_6px_hsl(var(--stark-ok))]",
        state === "warn" && "bg-[hsl(var(--stark-hot))] shadow-[0_0_6px_hsl(var(--stark-hot))]",
        state === "off" && "border border-muted-foreground/60",
      )}
    />
  );
}

/** How many readings each sparkline keeps (one per five-second poll). */
const SPARK_POINTS = 36;

/**
 * SYSTEM RESOURCES: CPU, GPU and RAM as rings with a sparkline of the last
 * three minutes and the hardware under each, then the brain's context fill.
 * Read from `/api/deck/vitals` every five seconds. A sensor that did not
 * answer draws an empty ring that reads "—", never a zero; a detail the
 * machine did not report is left out.
 */
export function ResourcesPanel({ className }: { className?: string }) {
  const t = useT();
  const q = useQuery<Vitals>({
    queryKey: ["deck", "vitals"],
    queryFn: fetchVitals,
    refetchInterval: 5_000,
    retry: false,
  });
  const v = q.data;
  const gpu = v?.gpus[0];
  const history = useHistory(v, q.dataUpdatedAt);
  const pct = (n: number | null | undefined) => (n == null ? "—" : `${Math.round(n)}%`);
  const gb = (mb: number | null | undefined) => (mb == null ? "—" : (mb / 1024).toFixed(1));
  const cores = v?.cpu_physical ?? null;
  const threads = v?.cpu_logical ?? null;
  const ghz = v?.cpu_mhz ? `${(v.cpu_mhz / 1000).toFixed(1)} GHz` : null;
  return (
    <StarkPanel title={t("stark.vitals.title")} className={className} testId="stark-vitals">
      {!v ? (
        <p className="py-6 text-center text-sm text-muted-foreground">
          {t(q.isError ? "deck.vitals_error" : "deck.vitals_loading")}
        </p>
      ) : (
        <div className="grid grid-cols-3 gap-2 pt-1">
          <ResourceColumn
            ring={<RingGauge value={v.cpu_percent / 100} label="CPU" inner readout={pct(v.cpu_percent)} size={92} testId="stark-ring-cpu" />}
            spark={history.cpu}
            line1={
              cores && threads
                ? fill(t("stark.vitals.cores_threads"), { cores, threads })
                : threads
                  ? fill(t("stark.vitals.threads"), { threads })
                  : null
            }
            line2={ghz}
            title={v.cpu_name ?? undefined}
          />
          <ResourceColumn
            ring={
              <RingGauge
                value={gpu?.util_percent != null ? gpu.util_percent / 100 : null}
                label="GPU"
                inner
                readout={pct(gpu?.util_percent)}
                size={92}
                testId="stark-ring-gpu"
              />
            }
            spark={history.gpu}
            line1={gpu ? `${gb(gpu.vram_used_mb)} / ${gb(gpu.vram_total_mb)} GB` : t("stark.vitals.no_gpu")}
            line2={gpu ? shortGpu(gpu.name) : null}
            title={gpu?.name}
          />
          <ResourceColumn
            ring={
              <RingGauge
                value={v.ram_total_gb > 0 ? v.ram_used_gb / v.ram_total_gb : null}
                label="RAM"
                inner
                readout={v.ram_total_gb > 0 ? `${Math.round((v.ram_used_gb / v.ram_total_gb) * 100)}%` : "—"}
                size={92}
                testId="stark-ring-ram"
              />
            }
            spark={history.ram}
            line1={`${v.ram_used_gb.toFixed(1)} / ${Math.round(v.ram_total_gb)} GB`}
            line2={gpu?.temp_c != null ? `GPU ${Math.round(gpu.temp_c)}°C` : null}
          />
        </div>
      )}
      <ContextRow />
    </StarkPanel>
  );
}

function ResourceColumn({
  ring,
  spark,
  line1,
  line2,
  title,
}: {
  ring: ReactNode;
  spark: number[];
  line1: string | null;
  line2: string | null;
  title?: string;
}) {
  return (
    <div className="flex min-w-0 flex-col items-center" title={title}>
      {ring}
      <Sparkline values={spark} className="mt-1 h-6 w-[88%]" />
      {line1 && <span className="mt-1 truncate text-sm font-medium tabular-nums text-foreground">{line1}</span>}
      {line2 && <span className="truncate text-xs font-medium text-muted-foreground">{line2}</span>}
    </div>
  );
}

/** "NVIDIA GeForce RTX 4060 Laptop GPU" reads "RTX 4060 Laptop". */
export function shortGpu(name: string): string {
  return name.replace(/^NVIDIA\s+(GeForce\s+)?/i, "").replace(/\s+GPU$/i, "").trim();
}

/** The last few readings of each ring, kept for this screen's lifetime. */
function useHistory(v: Vitals | undefined, at: number) {
  const ref = useRef<{ at: number; cpu: number[]; gpu: number[]; ram: number[] }>({ at: 0, cpu: [], gpu: [], ram: [] });
  const [, bump] = useState(0);
  useEffect(() => {
    if (!v || at === ref.current.at) return;
    const push = (list: number[], value: number | null | undefined) =>
      value == null ? list : [...list, Math.max(0, Math.min(1, value))].slice(-SPARK_POINTS);
    const gpu = v.gpus[0]?.util_percent;
    ref.current = {
      at,
      cpu: push(ref.current.cpu, v.cpu_percent / 100),
      gpu: push(ref.current.gpu, gpu == null ? null : gpu / 100),
      ram: push(ref.current.ram, v.ram_total_gb > 0 ? v.ram_used_gb / v.ram_total_gb : null),
    };
    bump((n) => n + 1);
  }, [v, at]);
  return ref.current;
}

/** A filled line of the recent readings, 0..1, newest on the right. */
export function Sparkline({ values, className }: { values: number[]; className?: string }) {
  if (values.length < 2) {
    return <div className={cn("border-b border-primary/25", className)} aria-hidden />;
  }
  const w = 100;
  const h = 24;
  const step = w / (SPARK_POINTS - 1);
  const x0 = w - (values.length - 1) * step;
  const pts = values.map((val, i) => `${(x0 + i * step).toFixed(1)},${(h - 1 - val * (h - 3)).toFixed(1)}`);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className={className} aria-hidden>
      <polygon points={`${x0},${h} ${pts.join(" ")} ${w},${h}`} fill="hsl(var(--primary) / 0.18)" />
      <polyline points={pts.join(" ")} fill="none" stroke="hsl(var(--primary))" strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/**
 * The brain's context on its newest turn, as one bar under the rings: the
 * count, the model's window and the 128k scale (the full meter's numbers,
 * `ContextMeter`). Without a local model reporting one it says so.
 */
function ContextRow() {
  const t = useT();
  const ctx = useDeckContext().data;
  const ready = Boolean(ctx?.available && typeof ctx.used === "number" && ctx.window);
  const used = ready ? (ctx!.used as number) : 0;
  const window = ready ? (ctx!.window as number) : 0;
  const max = Math.max(METER_MAX, window);
  const ofWindow = window ? used / window : 0;
  return (
    <div className="mt-3 border-t border-primary/15 pt-2" data-testid="stark-context-row" data-used={ready ? used : ""}>
      <div className="flex items-baseline justify-between text-xs font-semibold uppercase tracking-[0.18em]">
        <span className="text-muted-foreground">{t("stark.context.title")}</span>
        <span className={cn("tabular-nums", ofWindow >= 0.85 ? "text-[hsl(var(--stark-hot))]" : "text-foreground")}>
          {ready ? `${fmtK(used)} / ${fmtK(window)}` : t("stark.context.none_short")}
        </span>
      </div>
      <div className="relative mt-1.5 h-1.5 bg-primary/10">
        {ready && (
          <>
            <div
              className={cn(
                "absolute inset-y-0 left-0",
                ofWindow >= 0.85 ? "bg-[hsl(var(--stark-hot))]" : "bg-primary shadow-[0_0_8px_hsl(var(--primary))]",
              )}
              style={{ width: `${(used / max) * 100}%` }}
            />
            <div className="absolute -inset-y-1 w-px bg-[hsl(var(--stark-hot))]" style={{ left: `${(window / max) * 100}%` }} />
          </>
        )}
      </div>
      {ctx?.settings && <ContextControls settings={ctx.settings} />}
    </div>
  );
}

const QUICK: Array<{ id: SectionId; icon: LucideIcon; key: string }> = [
  { id: "visualization", icon: FolderOpen, key: "files" },
  { id: "armory", icon: LayoutGrid, key: "apps" },
  { id: "world-intelligence", icon: Globe2, key: "world" },
  { id: "agentic-ide", icon: Code2, key: "code" },
  { id: "memory", icon: Brain, key: "memory" },
  { id: "agents", icon: Bot, key: "agents" },
  { id: "skills", icon: Sparkles, key: "skills" },
  { id: "costs", icon: Coins, key: "costs" },
];

/** QUICK ACCESS: the sections people open most, one press away. */
export function QuickAccess({ className }: { className?: string }) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  return (
    <StarkPanel title={t("stark.quick.title")} className={className} testId="stark-quick">
      <div className="grid grid-cols-4 gap-x-1 gap-y-3 pt-1">
        {QUICK.map(({ id, icon: Icon, key }) => (
          <button
            key={id}
            type="button"
            onClick={() => setActiveSection(id)}
            className="group flex flex-col items-center gap-1.5 rounded py-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
            data-testid={`stark-quick-${key}`}
          >
            <Icon className="h-6 w-6 text-foreground/85 transition-colors group-hover:text-primary" strokeWidth={1.4} aria-hidden />
            <span className="text-xs font-semibold text-muted-foreground group-hover:text-foreground">
              {t(`stark.quick.${key}`)}
            </span>
          </button>
        ))}
      </div>
    </StarkPanel>
  );
}

/** A compact clock: local time to the second and the date, in the HUD's type. */
export function StarkClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const time = now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const date = now.toLocaleDateString(undefined, { weekday: "short", day: "2-digit", month: "short", year: "numeric" });
  return (
    <div className="flex flex-col items-end leading-none" data-testid="stark-clock-compact">
      <span className="font-mono text-lg tabular-nums text-foreground">{time}</span>
      <span className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">{date}</span>
    </div>
  );
}

