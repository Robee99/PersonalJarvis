import type { ThinkingStep } from "@/lib/thinkingSteps";

/**
 * Summarize only observable work for the Mission Control progress card.
 * Model reasoning and provider setup are deliberately omitted.
 */
export function missionProgress(steps: ThinkingStep[]) {
  const visible = steps.filter((step) =>
    step.kind === "tool" || step.kind === "computer" || step.kind === "worker" || step.kind === "note",
  );
  return {
    steps: visible.slice(-5),
    total: visible.length,
    done: visible.filter((step) => step.status === "done").length,
    active: visible.filter((step) => step.status === "active").length,
    failed: visible.filter((step) => step.status === "error").length,
  };
}
