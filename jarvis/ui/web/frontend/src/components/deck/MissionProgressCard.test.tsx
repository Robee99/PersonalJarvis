import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MissionProgressCard } from "./MissionProgressCard";
import type { ThinkingStep } from "@/lib/thinkingSteps";

describe("MissionProgressCard", () => {
  it("shows observable tool progress and updates its progress bar", () => {
    const steps: ThinkingStep[] = [
      {
        id: "tool-1",
        kind: "tool",
        labelKey: "thinking.step_tool",
        detail: "filesystem.read_file",
        status: "done",
        startedTs: 1,
        durationMs: 20,
      },
      {
        id: "tool-2",
        kind: "tool",
        labelKey: "thinking.step_tool",
        detail: "windows.click",
        status: "active",
        startedTs: 2,
      },
    ];

    render(<MissionProgressCard steps={steps} />);

    const progress = screen.getByRole("progressbar");
    expect(progress.getAttribute("aria-valuenow")).toBe("1");
    expect(progress.getAttribute("aria-valuemax")).toBe("2");
    expect(screen.getByText("filesystem.read_file")).toBeTruthy();
    expect(screen.getByText("windows.click")).toBeTruthy();
    expect(document.querySelector('[data-testid="mission-progress-steps"] [data-status="active"]')).toBeTruthy();
  });

  it("does not render when the trace has no observable work", () => {
    const reasoning: ThinkingStep[] = [
      {
        id: "thought-1",
        kind: "thought",
        labelKey: "thinking.step_thought",
        detail: "Planning",
        status: "active",
        startedTs: 1,
      },
    ];

    const { container } = render(<MissionProgressCard steps={reasoning} />);
    expect(container.textContent).toBe("");
  });
});
