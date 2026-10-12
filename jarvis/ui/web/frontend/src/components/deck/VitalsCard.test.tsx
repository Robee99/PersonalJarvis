import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { VitalsCard, type Vitals } from "@/components/deck/DeckSignalCards";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderWith(body: Vitals | null) {
  vi.stubGlobal("fetch", async () =>
    body ? new Response(JSON.stringify(body)) : new Response("", { status: 500 }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <VitalsCard />
    </QueryClientProvider>,
  );
}

const LAPTOP: Vitals = {
  cpu_percent: 37.2,
  cpu_logical: 16,
  ram_used_gb: 10.5,
  ram_total_gb: 15.3,
  battery: { percent: 82, plugged: true },
  gpus: [
    {
      name: "NVIDIA GeForce RTX 4060 Laptop GPU",
      util_percent: 12,
      vram_used_mb: 1536,
      vram_total_mb: 8188,
      temp_c: 48,
      power_w: 9.87,
    },
  ],
  power_mode: "performance",
};

describe("VitalsCard", () => {
  it("shows the live reading of an RTX laptop", async () => {
    renderWith(LAPTOP);

    const card = await screen.findByTestId("deck-vitals");
    const text = card.textContent ?? "";
    expect(text).toContain("37%");
    expect(text).toContain("10.5G");
    expect(text).toContain("12%");
    expect(text).toContain("1.5 / 8.0 GB");
    expect(text).toContain("48 °C");
    expect(text).toContain("82 %");
    expect(text).toContain("performance");
    expect(screen.getByText("RTX 4060 Laptop GPU")).toBeTruthy();
  });

  it("shows no GPU gauge or sensor rows nobody measured", async () => {
    renderWith({ ...LAPTOP, battery: null, gpus: [], power_mode: null });

    const card = await screen.findByTestId("deck-vitals");
    expect(card.textContent).not.toContain("GPU");
    expect(card.querySelector("ul")).toBeNull();
  });

  it("says so when the machine gives no reading", async () => {
    renderWith(null);

    expect(await screen.findByText("No reading from this machine.")).toBeTruthy();
  });
});
