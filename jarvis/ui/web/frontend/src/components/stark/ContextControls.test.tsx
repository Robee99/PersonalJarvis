import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ContextControls } from "@/components/stark/ContextControls";
import type { DeckContextSettings } from "@/components/stark/ContextMeter";
import { useI18nStore } from "@/i18n";

function withQuery(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

const SETTINGS: DeckContextSettings = {
  loaded: 65_536,
  choices: [8_192, 32_768, 65_536, 131_072],
  vision_on_gpu: false,
  vision_gpu_max: 32_768,
};

/** A fake model server behind /api/deck/context: records each POST body. */
function fakeServer(answer: (ctx: number | null) => { status: number; body: unknown }) {
  const posts: Array<number | null> = [];
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input) !== "/api/deck/context" || init?.method !== "POST") return new Response("{}", { status: 404 });
    const ctx = (JSON.parse(String(init.body)) as { ctx: number | null }).ctx;
    posts.push(ctx);
    const { status, body } = answer(ctx);
    return new Response(JSON.stringify(body), { status });
  });
  return posts;
}

describe("context slider", () => {
  beforeEach(() => useI18nStore.getState().setUi("en", { push: false }));
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  test("moving the slider picks a size; Apply restarts the model with it", async () => {
    const posts = fakeServer((ctx) => ({ status: 200, body: { ok: true, window: ctx, vision_on_gpu: true } }));
    withQuery(<ContextControls settings={SETTINGS} />);
    const apply = screen.getByTestId("stark-context-apply") as HTMLButtonElement;
    expect(apply.disabled).toBe(true); // the loaded size: nothing to apply
    expect(screen.getByTestId("stark-context-picked").textContent).toBe("64k");

    fireEvent.change(screen.getByTestId("stark-context-slider"), { target: { value: "1" } });
    expect(screen.getByTestId("stark-context-picked").textContent).toBe("32k");
    expect(screen.getByTestId("stark-context-note").textContent).toContain("GPU");
    fireEvent.click(apply);

    await waitFor(() => expect(screen.getByTestId("stark-context-note").textContent).toContain("restarted with 32k"));
    expect(posts).toEqual([32_768]);
  });

  test("a size that did not load says which size was kept", async () => {
    fakeServer(() => ({ status: 409, body: { ok: false, window: 65_536, error: "131072 did not load; kept 65536" } }));
    withQuery(<ContextControls settings={SETTINGS} />);
    fireEvent.change(screen.getByTestId("stark-context-slider"), { target: { value: "3" } });
    expect(screen.getByTestId("stark-context-note").textContent).toContain("CPU");
    fireEvent.click(screen.getByTestId("stark-context-apply"));
    await waitFor(() => expect(screen.getByTestId("stark-context-note").textContent).toContain("kept 65536"));
  });

  test("reload restarts the model as it is", async () => {
    const posts = fakeServer(() => ({ status: 200, body: { ok: true, window: 65_536 } }));
    withQuery(<ContextControls settings={SETTINGS} />);
    fireEvent.click(screen.getByTestId("stark-context-reload"));
    await waitFor(() => expect(screen.getByTestId("stark-context-note").textContent).toContain("reloaded"));
    expect(posts).toEqual([null]);
  });

  test("without a choice of sizes there is no slider", () => {
    withQuery(<ContextControls settings={{ ...SETTINGS, choices: [65_536] }} />);
    expect(screen.queryByTestId("stark-context-controls")).toBeNull();
  });
});
