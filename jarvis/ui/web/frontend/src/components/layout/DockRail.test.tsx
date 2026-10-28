import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { DOCK_RAIL_GEOMETRY, DockRail } from "@/components/layout/DockRail";
import { NAV_FOOTER_ITEMS, NAV_GROUPS } from "@/components/layout/navGroups";
import { useEventStore } from "@/store/events";

// The retired catalog hook must not be polled by the navigation rail.
const pluginAttentionMock = vi.hoisted(() => ({
  calls: 0,
  needsReconnect: false,
}));
vi.mock("@/hooks/usePluginAttention", () => ({
  usePluginAttention: () => {
    pluginAttentionMock.calls += 1;
    return pluginAttentionMock.needsReconnect
      ? { count: 1, names: ["Cloudflare"] }
      : { count: 0, names: [] };
  },
}));

// The rail lists the footer's Feedback row too, behind one more hairline.
const ITEMS = [...NAV_GROUPS.flat(), ...NAV_FOOTER_ITEMS];
const { BASE, GAP, PAD_TOP } = DOCK_RAIL_GEOMETRY;

/** clientY that lands on the centre of icon `i` (jsdom rects sit at 0,0). */
function centreOf(i: number): number {
  return PAD_TOP + GAP + i * (BASE + GAP) + BASE / 2;
}

/** jsdom has no PointerEvent; a MouseEvent under the pointer name carries the
 *  clientY the rail reads. */
function moveTo(rail: HTMLElement, clientY: number) {
  act(() => {
    rail.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientY }));
  });
}
function leave(rail: HTMLElement) {
  act(() => {
    // React derives onPointerLeave from pointerout + relatedTarget.
    rail.dispatchEvent(
      new MouseEvent("pointerout", { bubbles: true, relatedTarget: document.body }),
    );
  });
}

function renderRail() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DockRail />
    </QueryClientProvider>,
  );
}

describe("DockRail", () => {
  beforeEach(() => {
    pluginAttentionMock.calls = 0;
    pluginAttentionMock.needsReconnect = false;
    useEventStore.setState({ activeSection: "chats", conversations: [] });
  });
  afterEach(() => cleanup());

  test("shows every section of the sidebar's NAV_GROUPS as one named button", () => {
    renderRail();
    for (const item of ITEMS) {
      expect(screen.getByTestId(`nav-row-${item.id}`)).toBeTruthy();
    }
    expect(screen.getAllByRole("button")).toHaveLength(ITEMS.length);
    expect(screen.getByTestId("nav-row-chats").getAttribute("aria-current")).toBe("page");
    // No native title: the rail draws its own label, and a browser tooltip on
    // top of it would be a second, late label.
    expect(screen.getByTestId("nav-row-chats").getAttribute("title")).toBeNull();
  });

  test("keeps the active control on a lit fill", () => {
    renderRail();
    expect(screen.getByTestId("nav-row-chats").classList).toContain("jarvis-nav-active");
    expect(screen.getByTestId("nav-row-board").classList).not.toContain(
      "jarvis-nav-active",
    );
  });

  test("hovering names exactly one icon, and crossing to the next one renames it", () => {
    renderRail();
    const rail = screen.getByTestId("dock-rail");

    expect(screen.queryByTestId("dock-label")).toBeNull();

    moveTo(rail, centreOf(2));
    let labels = screen.getAllByTestId("dock-label");
    expect(labels).toHaveLength(1);
    expect(labels[0].textContent).toContain(
      screen.getByTestId(`nav-row-${ITEMS[2].id}`).getAttribute("aria-label"),
    );

    moveTo(rail, centreOf(3));
    labels = screen.getAllByTestId("dock-label");
    expect(labels).toHaveLength(1);
    expect(labels[0].textContent).toContain(
      screen.getByTestId(`nav-row-${ITEMS[3].id}`).getAttribute("aria-label"),
    );
  });

  test("the label leaves the rail's stacking context — a portal on <body>, fixed to the viewport", () => {
    // The sidebar column paints at z-20 in the same stacking context as the
    // section stage, so a section's own z-20 layer (the IDE's pane chat) ties
    // with it and wins on DOM order: "Local models" was cut to "Lo" at the
    // rail's edge (report 2026-08-27). The label therefore rides outside the
    // column entirely, at the tooltip level.
    renderRail();
    const rail = screen.getByTestId("dock-rail");
    moveTo(rail, centreOf(2));
    const label = screen.getByTestId("dock-label");
    expect(label.parentElement).toBe(document.body);
    expect(rail.contains(label)).toBe(false);
    expect(label.classList).toContain("fixed");
    expect(label.classList).toContain("z-[70]");
  });

  test("hovering does not grow or move an icon — the rail holds its size and place", () => {
    // The magnification was taken back by the maintainer (2026-08-18): the
    // hovered icon gets its surface and its label, nothing else changes.
    renderRail();
    const rail = screen.getByTestId("dock-rail");
    const before = ITEMS.map((item) => {
      const el = screen.getByTestId(`nav-row-${item.id}`);
      return [el.style.top, el.style.width, el.style.height].join("|");
    });

    moveTo(rail, centreOf(2));
    const during = ITEMS.map((item) => {
      const el = screen.getByTestId(`nav-row-${item.id}`);
      return [el.style.top, el.style.width, el.style.height].join("|");
    });
    expect(during).toEqual(before);
    expect(screen.getByTestId(`nav-row-${ITEMS[2].id}`).style.width).toBe("30px");
  });

  test("leaving the rail takes the label away", async () => {
    renderRail();
    const rail = screen.getByTestId("dock-rail");
    moveTo(rail, centreOf(1));
    expect(screen.getAllByTestId("dock-label")).toHaveLength(1);
    leave(rail);
    await waitFor(() => expect(screen.queryByTestId("dock-label")).toBeNull(), {
      timeout: 2000,
    });
  });

  test("picking an icon jumps to its section", () => {
    renderRail();
    act(() => {
      fireEvent.click(screen.getByTestId("nav-row-board"));
    });
    expect(useEventStore.getState().activeSection).toBe("board");
  });

  test("keyboard focus names the icon too, so the rail is not a row of blank glyphs", () => {
    renderRail();
    act(() => {
      fireEvent.focus(screen.getByTestId("nav-row-docs"));
    });
    const labels = screen.getAllByTestId("dock-label");
    expect(labels).toHaveLength(1);
    expect(labels[0].textContent).toContain(
      screen.getByTestId("nav-row-docs").getAttribute("aria-label"),
    );
  });

  test("does not poll or expose the retired plugin catalog in navigation", () => {
    renderRail();
    expect(pluginAttentionMock.calls).toBe(0);
    expect(screen.queryByTestId("nav-row-plugins")).toBeNull();
    expect(screen.queryByTestId("nav-row-skills")).toBeNull();
    expect(screen.queryByTestId("nav-row-mcps")).toBeNull();
  });
});
