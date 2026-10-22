import { describe, expect, it } from "vitest";
import { missionProgress } from "@/lib/missionProgress";
import type { ThinkingStep } from "@/lib/thinkingSteps";

const step = (id: string, kind: ThinkingStep["kind"], status: ThinkingStep["status"]): ThinkingStep => ({
  id,
  kind,
  status,
  labelKey: `thinking.${id}`,
  startedTs: 1,
});

describe("missionProgress", () => {
  it("summarizes real tool, computer, worker, and progress-note events", () => {
    const result = missionProgress([
      step("tool", "tool", "done"),
      step("computer", "computer", "active"),
      step("worker", "worker", "error"),
      step("note", "note", "done"),
      step("brain", "brain", "done"),
      step("thought", "thought", "active"),
    ]);
    expect(result).toMatchObject({ total: 4, done: 2, active: 1, failed: 1 });
    expect(result.steps.map(({ kind }) => kind)).toEqual(["tool", "computer", "worker", "note"]);
  });

  it("keeps only the latest five visible events without losing full counts", () => {
    const result = missionProgress(Array.from({ length: 7 }, (_, i) => step(`${i}`, "tool", "done")));
    expect(result.total).toBe(7);
    expect(result.done).toBe(7);
    expect(result.steps.map(({ id }) => id)).toEqual(["2", "3", "4", "5", "6"]);
  });

  it("does not expose model reasoning rows as mission activity", () => {
    expect(missionProgress([step("thought", "thought", "active")])).toMatchObject({
      steps: [], total: 0, done: 0, active: 0, failed: 0,
    });
  });
});
