import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ArcHalo } from "@/components/stark/ArcHalo";
import { ContextMeter, fmtK } from "@/components/stark/ContextMeter";
import { readCity, weatherKey } from "@/components/stark/WeatherGlobe";
import { wordmark } from "@/views/MissionDeckView";
import { useI18nStore } from "@/i18n";

function withQuery(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

function stubContext(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/deck/context") return new Response(JSON.stringify(body), { status: 200 });
      return new Response("{}", { status: 404 });
    }),
  );
}

describe("Stark HUD instruments", () => {
  beforeEach(() => useI18nStore.getState().setUi("en", { push: false }));
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  test("the context meter shows the brain's own count against its window, with the limit and trim marks", async () => {
    stubContext({ available: true, used: 31_000, window: 65_536, model: "qwen3-vl-8b", at: 1, compress_at: 56_000 });
    withQuery(<ContextMeter />);
    const meter = await screen.findByTestId("stark-context-meter");
    expect(meter.dataset.used).toBe("31000");
    expect(meter.dataset.window).toBe("65536");
    expect(meter.textContent).toContain("30.3k");
    expect(meter.textContent).toContain("47%");
    expect(meter.textContent).toContain("Limit 64k");
    expect(meter.textContent).toContain("Trim 54.7k");
    // The scale runs to 128k: the model's 64k window is the line, not the end.
    expect(meter.textContent).toContain("128k");
  });

  test("without a swapper the meter says no local model reports a context", async () => {
    stubContext({ available: false });
    withQuery(<ContextMeter />);
    await waitFor(() => expect(screen.getByTestId("stark-context-empty")).toBeTruthy());
  });

  test("the halo draws the context arc only when there is a reading", () => {
    const { rerender } = render(
      <ArcHalo size={400} voiceState="idle" busy={false} context={null} title="J.A.R.V.I.S.">
        <span />
      </ArcHalo>,
    );
    expect(screen.queryByTestId("stark-halo-context")).toBeNull();
    rerender(
      <ArcHalo size={400} voiceState="listening" busy={false} context={{ used: 32_768, window: 65_536 }} title="J.A.R.V.I.S.">
        <span />
      </ArcHalo>,
    );
    expect(screen.getByTestId("stark-halo-context")).toBeTruthy();
    expect(screen.getByTestId("stark-halo").dataset.voice).toBe("listening");
  });

  test("helpers: token counts, the wordmark, weather words and the stored city", () => {
    expect(fmtK(950)).toBe("950");
    expect(fmtK(65_536)).toBe("64k");
    expect(fmtK(131_072)).toBe("128k");
    expect(wordmark("Jarvis")).toBe("J.A.R.V.I.S.");
    expect(wordmark("Nova")).toBe("NOVA");
    expect(weatherKey(0)).toBe("clear");
    expect(weatherKey(63)).toBe("rain");
    expect(weatherKey(95)).toBe("storm");
    expect(readCity()).toBeNull();
    window.localStorage.setItem("stark.weather.city.v1", JSON.stringify({ name: "Pune", country: "India", lat: 18.52, lon: 73.86 }));
    expect(readCity()?.name).toBe("Pune");
    window.localStorage.setItem("stark.weather.city.v1", "{broken");
    expect(readCity()).toBeNull();
  });
});
