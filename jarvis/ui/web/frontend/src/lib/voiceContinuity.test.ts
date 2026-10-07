import { describe, expect, it } from "vitest";

import { resolveVoiceContinuity, type ChatPick } from "@/lib/voiceContinuity";

const pick = (id: string, runner: string, model = ""): ChatPick => ({ id, label: id, runner, model });

describe("resolveVoiceContinuity", () => {
  it("has nothing to compare without a chat pick", () => {
    expect(resolveVoiceContinuity(null, { tier: "realtime", providerId: "gemini" }).kind).toBe("none");
  });

  it("calls a Pipeline brain on the chat's provider the same engine", () => {
    expect(resolveVoiceContinuity(pick("nous", "api"), { tier: "pipeline", providerId: "nous" }).kind).toBe("same");
  });

  it("never calls a realtime provider of the same family the same engine", () => {
    // Gemini Live is not the Gemini text brain the chat picked.
    const r = resolveVoiceContinuity(pick("gemini", "api"), { tier: "realtime", providerId: "gemini" });
    expect(r).toMatchObject({ kind: "switchable", needsPipeline: true });
  });

  it("offers the switch for an API chat provider, flagging when voice must leave Realtime", () => {
    expect(resolveVoiceContinuity(pick("local-openai", "api"), { tier: "pipeline", providerId: "gemini" }))
      .toMatchObject({ kind: "switchable", needsPipeline: false });
    expect(resolveVoiceContinuity(pick("openrouter", "brain"), { tier: "realtime", providerId: "gemini-live" }))
      .toMatchObject({ kind: "switchable", needsPipeline: true });
  });

  it("explains instead of switching for a CLI agent such as Hermes Agent", () => {
    expect(resolveVoiceContinuity(pick("hermes", "hermes-cli"), { tier: "realtime", providerId: "gemini" }).kind).toBe("cli");
    expect(resolveVoiceContinuity(pick("claude-api", "claude-cli"), { tier: "pipeline", providerId: "claude-api" }).kind).toBe("cli");
  });
});
