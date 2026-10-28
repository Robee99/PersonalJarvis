import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MainView } from "./MainView";
import { useEventStore } from "@/store/events";

vi.mock("@/views/ChatsSurface", () => ({
  ChatsSurface: () => <input aria-label="Unsent message" defaultValue="A draft" />,
}));
beforeEach(() => { useEventStore.setState({ activeSection: "chats", solo: false, detachedViews: [] }); });
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it.each(["armory", "plugins", "mcps", "skills", "marketplace"] as const)(
  "returns old %s links to chat without opening a catalog",
  async (activeSection) => {
    render(<MainView />);
    const draft = screen.getByLabelText("Unsent message");
    fireEvent.change(draft, { target: { value: "Keep this draft" } });

    act(() => useEventStore.getState().setActiveSection(activeSection));

    await waitFor(() => expect(useEventStore.getState().activeSection).toBe("chats"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByLabelText("Unsent message")).toBe(draft);
    expect((screen.getByLabelText("Unsent message") as HTMLInputElement).value).toBe("Keep this draft");
  },
);
