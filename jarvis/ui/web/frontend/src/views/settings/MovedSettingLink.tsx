import { ArrowRight } from "lucide-react";
import type { ReactNode } from "react";

/**
 * Where a duplicate control used to be: one line saying where the setting
 * lives now and a button that goes there.
 *
 * The forensic UI audit (2026-10-07, P0-3) found the Realtime toggle in two
 * places and the speech-to-text provider in two places; each pair wrote the
 * same config. One copy of each stays (Settings › Voice); the other became
 * this link, so the person who looks in the old place is sent to the one
 * place instead of finding a second switch.
 */
export function MovedSettingLink({
  icon,
  title,
  description,
  actionLabel,
  onOpen,
  testId,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  actionLabel: string;
  onOpen: () => void;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      className="mt-2 flex items-start gap-3 rounded-lg border border-border bg-card p-4"
    >
      <span className="mt-0.5 shrink-0 text-muted-foreground [&>svg]:h-4 [&>svg]:w-4">{icon}</span>
      <div className="min-w-0 flex-1">
        <h4 className="font-medium text-foreground">{title}</h4>
        <p className="mt-0.5 text-meta text-muted-foreground">{description}</p>
      </div>
      <button
        type="button"
        onClick={onOpen}
        className="inline-flex shrink-0 items-center gap-1.5 self-center rounded-md border border-border px-3 py-1.5 text-meta font-medium text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {actionLabel}
        <ArrowRight aria-hidden className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
