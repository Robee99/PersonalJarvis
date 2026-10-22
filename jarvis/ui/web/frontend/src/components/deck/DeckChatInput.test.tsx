import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DeckChatInput } from "@/components/deck/DeckChatInput";
import { useEventStore } from "@/store/events";

const { sendChatMessage } = vi.hoisted(() => ({ sendChatMessage: vi.fn() }));

vi.mock("@/lib/chat", () => ({ sendChatMessage }));

describe("DeckChatInput", () => {
  beforeEach(() => {
    sendChatMessage.mockReset();
    sendChatMessage.mockResolvedValue(true);
    useEventStore.setState({ connected: true, chatThinking: false });
  });

  it("sends through the shared persisted chat path and clears after dispatch", async () => {
    render(<DeckChatInput />);
    const input = screen.getByRole("textbox", { name: "Message JARVIS" });
    fireEvent.change(input, { target: { value: "  Open the memory galaxy  " } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(sendChatMessage).toHaveBeenCalledWith("Open the memory galaxy"));
    await waitFor(() => expect((input as HTMLInputElement).value).toBe(""));
  });

  it("keeps the message when the shared chat transport is unavailable", async () => {
    sendChatMessage.mockResolvedValue(false);
    render(<DeckChatInput />);
    const input = screen.getByRole("textbox", { name: "Message JARVIS" });
    fireEvent.change(input, { target: { value: "Check MCP status" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(sendChatMessage).toHaveBeenCalledWith("Check MCP status"));
    expect((input as HTMLInputElement).value).toBe("Check MCP status");
  });

  it("does not offer sending while the backend link is down", () => {
    useEventStore.setState({ connected: false });
    render(<DeckChatInput />);

    expect((screen.getByRole("textbox", { name: "Message JARVIS" }) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Send message" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
