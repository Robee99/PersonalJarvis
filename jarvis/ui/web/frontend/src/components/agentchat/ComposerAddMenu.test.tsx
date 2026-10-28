import { useRef, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ComposerAddMenu } from "./ComposerAddMenu";
import { ToolChoiceChips } from "./ToolChoiceChips";
import { readToolChoices, type ToolChoice } from "./toolChoices";
import { sendAgentChatMessage } from "@/lib/agentChatApi";
import gmailLogo from "@/assets/brands/gmail.svg?url";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));

const gmail: ToolChoice = {
  id: "plugin:gmail",
  label: "Gmail",
  brand: "gmail",
  category: "plugins",
  group: "Gmail",
  description: "Read your inbox",
  available: true,
  tool_names: ["gmail"],
  skill: "",
};
const disconnected = {
  ...gmail,
  id: "plugin:offline",
  label: "Offline",
  available: false,
};
const pcPilot: ToolChoice = {
  id: "tool:pc_pilot",
  label: "PC Pilot",
  brand: "",
  category: "system",
  group: "system",
  description: "Operate the desktop",
  available: true,
  tool_names: ["pc_pilot"],
  skill: "",
};
const filesystem: ToolChoice = {
  ...pcPilot,
  id: "mcp:filesystem",
  label: "Filesystem",
  category: "mcp",
  group: "filesystem",
  description: "Read and manage local files",
  tool_names: ["filesystem/read_file"],
};
const windowsMcp: ToolChoice = {
  ...filesystem,
  id: "mcp:windows-mcp",
  label: "Windows",
  group: "windows-mcp",
  tool_names: ["windows-mcp/list_windows"],
};
const browserHarness: ToolChoice = {
  ...filesystem,
  id: "mcp:browser-harness",
  label: "Browser harness",
  group: "browser-harness",
  tool_names: ["browser-harness/browser_navigate"],
};
const godsEye: ToolChoice = {
  ...filesystem,
  id: "mcp:gev",
  label: "gev",
  category: "mcp",
  group: "gev",
  description: "Navigate the God's Eye globe",
  tool_names: ["gev/show_in_gods_eye_view"],
};
const worldGlobe: ToolChoice = {
  ...pcPilot,
  id: "tool:world_globe",
  label: "God's Eye globe",
  description: "Control the God's Eye globe",
  tool_names: ["world_globe"],
};
const tencentBrowserSkill: ToolChoice = {
  ...gmail,
  id: "skill:tencent-browser-skill",
  label: "Tencent BrowserSkill",
  category: "skills",
  group: "skills",
  skill: "tencent-browser-skill",
  tool_names: ["run-skill"],
};
const unrelatedMcp: ToolChoice = {
  ...filesystem,
  id: "mcp:github",
  label: "GitHub",
  group: "github",
  tool_names: ["github/get_me"],
};

function setup(
  items = [gmail, disconnected],
  onInsert?: (row: ToolChoice) => void,
  initialScope: "computer" | "all" = "computer",
) {
  const connect = vi.fn(),
    attach = vi.fn(),
    folder = vi.fn(),
    insert = vi.fn(onInsert);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ items, mode: "browse", total: items.length }))),
  );
  function Harness() {
    const anchor = useRef<HTMLDivElement>(null);
    const [selected, setSelected] = useState<ToolChoice[]>([]);
    return (
      <div ref={anchor}>
        <ToolChoiceChips
          items={selected}
          onRemove={(id) => setSelected(selected.filter((r) => r.id !== id))}
        />
        <ComposerAddMenu
          anchorRef={anchor}
          provider="openai"
          model="test"
          cwd=""
          stance="ask"
          selected={selected}
          onChange={setSelected}
          onInsert={onInsert ? insert : undefined}
          onAttach={attach}
          onFolder={folder}
          onConnect={connect}
          disabled={false}
        />
      </div>
    );
  }
  render(<Harness />);
  fireEvent.click(screen.getByTestId("composer-add"));
  if (initialScope === "all") {
    fireEvent.click(screen.getByRole("button", { name: "chat_tools.all" }));
  }
  return { connect, attach, folder, insert };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Add menu", () => {
  it("opens on PC controls and keeps God's Eye and Tencent BrowserSkill visible", async () => {
    setup([
      pcPilot,
      filesystem,
      windowsMcp,
      browserHarness,
      godsEye,
      worldGlobe,
      tencentBrowserSkill,
      gmail,
      unrelatedMcp,
    ]);

    expect(await screen.findByRole("button", { name: /PC Pilot Operate the desktop/ })).toBeTruthy();
    expect(await screen.findByRole("button", { name: /Filesystem Read and manage local files/ })).toBeTruthy();
    expect(await screen.findByRole("button", { name: /Windows Read and manage local files/ })).toBeTruthy();
    expect(await screen.findByRole("button", { name: /Browser harness Read and manage local files/ })).toBeTruthy();
    expect(await screen.findByRole("button", { name: /gev Navigate the God's Eye globe/ })).toBeTruthy();
    expect(await screen.findByRole("button", { name: /God's Eye globe Control the God's Eye globe/ })).toBeTruthy();
    expect(await screen.findByRole("button", { name: /Tencent BrowserSkill Read your inbox/ })).toBeTruthy();
  });

  it("hides unrelated connected plugins from the default view", async () => {
    setup([pcPilot, gmail, unrelatedMcp]);

    expect(await screen.findByRole("button", { name: /PC Pilot Operate the desktop/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Gmail Read your inbox/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /GitHub Read and manage local files/ })).toBeNull();
  });

  it("switches to all tools without clearing selections", async () => {
    setup([pcPilot, gmail]);

    fireEvent.click(
      await screen.findByRole("button", { name: /PC Pilot Operate the desktop/ }),
    );
    fireEvent.click(screen.getByRole("button", { name: "chat_tools.all" }));
    fireEvent.click(await screen.findByRole("button", { name: /Gmail Read your inbox/ }));
    fireEvent.click(screen.getByRole("button", { name: "chat_tools.computer_control" }));

    expect(screen.queryByRole("button", { name: /Gmail Read your inbox/ })).toBeNull();
    expect(screen.getByTestId("tool-choice-chips").textContent).toContain("PC Pilot");
    expect(screen.getByTestId("tool-choice-chips").textContent).toContain("Gmail");
  });

  it("shows the original Gmail mark and toggles removable selections", async () => {
    setup([gmail, disconnected], undefined, "all");
    const row = await screen.findByRole("button", {
      name: /Gmail Read your inbox/,
    });
    expect(row.querySelector("img")?.getAttribute("src")).toBe(gmailLogo);
    fireEvent.click(row);
    expect(row.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("tool-choice-chips").textContent).toContain("Gmail");
    fireEvent.click(screen.getByRole("button", { name: "chat_tools.remove Gmail" }));
    expect(row.getAttribute("aria-pressed")).toBe("false");
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(screen.getByTestId("composer-add"));
  });

  it("opens setup for unavailable plugins once they are searched for", async () => {
    const { connect } = setup([gmail, disconnected], undefined, "all");
    await screen.findByRole("button", { name: /Gmail Read your inbox/ });
    expect(screen.queryByRole("button", { name: /Offline Read your inbox/ })).toBeNull();
    fireEvent.change(screen.getByLabelText("chat_tools.search"), {
      target: { value: "Offline" },
    });
    fireEvent.click(
      await screen.findByRole("button", { name: /Offline Read your inbox/ }, { timeout: 2000 }),
    );
    expect(connect).toHaveBeenCalledOnce();
    expect(screen.queryByTestId("tool-choice-chips")).toBeNull();
  });

  it("lists the connected plugin, not its commands or bundled skill", async () => {
    setup([
      gmail,
      { ...gmail, id: "tool:gmail/send", label: "send", description: "Send a message" },
      {
        ...gmail,
        id: "skill:plugin-gmail",
        label: "plugin-gmail",
        category: "skills",
        group: "skills",
        skill: "plugin-gmail",
        description: "Use Gmail",
      },
    ], undefined, "all");
    expect(await screen.findByRole("button", { name: /Gmail Read your inbox/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /send Send a message/ })).toBeNull();
    expect(screen.queryByText("plugin-gmail")).toBeNull();
  });

  it("keeps the category filter available in All tools and forwards it with the query", async () => {
    setup([gmail, disconnected], undefined, "all");
    await screen.findByRole("button", { name: /Gmail Read your inbox/ });
    fireEvent.click(screen.getByRole("combobox", { name: "chat_tools.filter" }));
    const memoryOption = await screen.findByRole("option", { name: "chat_tools.memory" });
    fireEvent.pointerDown(memoryOption);
    fireEvent.click(memoryOption);
    fireEvent.change(screen.getByLabelText("chat_tools.search"), {
      target: { value: "g" },
    });
    await waitFor(
      () => {
        const calls = vi.mocked(fetch).mock.calls;
        const url = new URL(String(calls[calls.length - 1][0]), "http://localhost");
        expect(url.searchParams.get("q")).toBe("g");
        expect(url.searchParams.get("category")).toBe("memory");
      },
      { timeout: 500 },
    );
  });

  it("closes after inserting a plugin so typing continues in the sentence", async () => {
    const { insert } = setup([gmail, disconnected], () => {}, "all");
    fireEvent.click(await screen.findByRole("button", { name: /Gmail Read your inbox/ }));
    expect(insert).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps file and folder actions functional", async () => {
    const { attach } = setup();
    fireEvent.click(screen.getByRole("button", { name: "chat_tools.attach" }));
    expect(attach).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("reports errors with retry instead of an empty result", async () => {
    setup();
    vi.mocked(fetch).mockRejectedValue(new Error("offline"));
    expect(await screen.findByRole("alert")).toBeDefined();
    expect(screen.getByRole("button", { name: "chat_tools.retry" })).toBeDefined();
  });

  it("sends only choice IDs and supports old receipts", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response('{"turn_id":"turn"}'));
    vi.stubGlobal("fetch", fetcher);
    await sendAgentChatMessage("session", "Read it", [], [gmail.id]);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual(
      expect.objectContaining({
        text: "Read it",
        attachments: [],
        tool_choices: ["plugin:gmail"],
      }),
    );
    expect(readToolChoices(undefined)).toEqual([]);
    expect(readToolChoices([gmail, { id: "invalid" }])).toEqual([gmail]);
  });
});
