import { ListChecks } from "lucide-react";
import type { ThinkingStep } from "@/lib/thinkingSteps";
import { missionProgress } from "@/lib/missionProgress";
import { DeckCard } from "@/components/deck/DeckCard";
import { HudLamp } from "@/components/deck/HudFrame";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/** A live progress checklist driven by JARVIS's actual tool and control events. */
export function MissionProgressCard({
  steps,
  className,
}: {
  steps: ThinkingStep[];
  className?: string;
}) {
  const t = useT();
  const progress = missionProgress(steps);
  if (!progress.total) return null;
  const percent = Math.round((progress.done / progress.total) * 100);

  return (
    <DeckCard
      icon={ListChecks}
      title={t("thinking.label")}
      meta={`${progress.done}/${progress.total}`}
      live={progress.active > 0}
      variant="chamfer"
      className={className}
      bodyClassName="flex min-h-0 flex-col p-0"
    >
      <div
        role="progressbar"
        aria-label={t("thinking.label")}
        aria-valuemin={0}
        aria-valuemax={progress.total}
        aria-valuenow={progress.done}
        className="h-0.5 shrink-0 overflow-hidden bg-border/60"
      >
        <div className="h-full bg-primary transition-[width] duration-300" style={{ width: `${percent}%` }} />
      </div>
      <ol data-testid="mission-progress-steps" className="min-h-0 flex-1 space-y-1 overflow-y-auto px-2.5 py-1.5">
        {progress.steps.map((step) => (
          <li
            key={step.id}
            data-status={step.status}
            className="flex min-w-0 items-center gap-2 text-micro"
          >
            <HudLamp on={step.status === "active"} />
            <span className={cn("min-w-0 flex-1 truncate", step.status === "error" && "text-destructive")}>
              {t(step.labelKey)}
            </span>
            {step.kind === "tool" && step.detail && (
              <span className="max-w-[45%] shrink-0 truncate font-mono text-muted-foreground" title={step.detail}>
                {step.detail}
              </span>
            )}
            <span className="sr-only">
              {step.status === "active" ? t("running") : step.status === "error" ? t("failed") : t("done")}
            </span>
          </li>
        ))}
      </ol>
    </DeckCard>
  );
}
