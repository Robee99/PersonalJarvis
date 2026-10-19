import { describe, expect, test } from "vitest";
import { originalConversations, stripImportMarkers } from "@/lib/importedSources";

describe("originalConversations", () => {
  test("links ChatGPT and Claude imports back to the original conversation", () => {
    const body = [
      "# Trip plan",
      '<!-- jarvis-conversation id="6710aa2e-1b2c-8000-9a1b-2c3d4e5f6a7b" format="chatgpt" created_ms="1700000000000" -->',
      "**You:** hi",
      '<!-- jarvis-turn id="m1" -->',
      '<!-- jarvis-conversation id="0b1c2d3e-4f5a-6b7c-8d9e-0f1a2b3c4d5e" format="claude" -->',
    ].join("\n");
    expect(originalConversations(body)).toEqual([
      { id: "6710aa2e-1b2c-8000-9a1b-2c3d4e5f6a7b", format: "chatgpt", url: "https://chatgpt.com/c/6710aa2e-1b2c-8000-9a1b-2c3d4e5f6a7b" },
      { id: "0b1c2d3e-4f5a-6b7c-8d9e-0f1a2b3c4d5e", format: "claude", url: "https://claude.ai/chat/0b1c2d3e-4f5a-6b7c-8d9e-0f1a2b3c4d5e" },
    ]);
  });

  test("an invented id, another export shape, or an odd id has no original", () => {
    const body = [
      '<!-- jarvis-conversation id="derived-0123456789abcdef" format="chatgpt" -->',
      '<!-- jarvis-conversation id="abcdef0123456789" format="messages" -->',
      '<!-- jarvis-conversation id="../../evil" format="claude" -->',
      '<!-- jarvis-conversation format="claude" -->',
    ].join("\n");
    expect(originalConversations(body)).toEqual([]);
    expect(originalConversations(undefined)).toEqual([]);
  });

  test("the same conversation twice is listed once", () => {
    const m = '<!-- jarvis-conversation id="0b1c2d3e-4f5a-6b7c" format="claude" -->';
    expect(originalConversations(`${m}\n${m}`)).toHaveLength(1);
  });

  test("the importer's markers are taken out of the text, nothing else", () => {
    const body = [
      "# Trip plan",
      "",
      '<!-- jarvis-conversation id="6710aa2e-1b2c-8000" format="chatgpt" created_ms="1700000000000" -->',
      "_Conversation imported from `export.json`_",
      "",
      '<!-- jarvis-turn id="m1" ts_ms="1700000000000" -->',
      "**You:**",
      "",
      "Keep <!-- this --> comment.",
    ].join("\n");
    expect(stripImportMarkers(body)).toBe(
      ["# Trip plan", "", "_Conversation imported from `export.json`_", "", "**You:**", "", "Keep <!-- this --> comment."].join("\n"),
    );
  });
});
