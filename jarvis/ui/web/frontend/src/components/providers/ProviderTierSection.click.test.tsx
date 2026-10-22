/**
 * The row CLICK gesture inside a provider tier list.
 *
 * On the brain tier a click only opens or closes the row: the brain is
 * picked in one place, the brain picker at the top of Settings › Model, so a
 * brain card holds its sign-in, key and model and never switches the brain.
 * (Other tiers keep "one click selects", see ProviderTierSection.test.tsx.)
 *
 * No jest-dom in this repo — assertions use plain values.
 */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { TierSection } from "@/components/providers/ProviderTierSection";
import type { ProviderDescriptor } from "@/hooks/useProviders";

function brainCard(over: Partial<ProviderDescriptor> = {}): ProviderDescriptor {
  return {
    id: "cloud-a",
    label: "Cloud A",
    tier: "brain",
    auth_mode: "api_key",
    secret_keys: ["cloud_a_api_key"],
    secrets_set: { cloud_a_api_key: true },
    dashboard_url: null,
    login_cli: null,
    install_hint: null,
    credential_path_hint: null,
    configured: true,
    active: false,
    cli_installed: null,
    credential_help: null,
    signup_url: null,
    billing: "api",
    alt_credential: null,
    ...over,
  };
}

let fetchMock: ReturnType<typeof vi.fn>;

/** The brain switches fired so far, as provider ids in order. */
function switches(): string[] {
  return fetchMock.mock.calls
    .filter(
      (c) =>
        String(c[0]).includes("/api/brain/switch") &&
        (c[1] as RequestInit | undefined)?.method === "POST",
    )
    .map((c) => JSON.parse((c[1] as RequestInit).body as string).provider as string);
}

function renderTier(providers: ProviderDescriptor[]) {
  const onActivateOptimistic = vi.fn();
  render(
    <TierSection
      providers={providers}
      onChanged={() => {}}
      onActivateOptimistic={onActivateOptimistic}
    />,
  );
  return { onActivateOptimistic };
}

beforeEach(() => {
  fetchMock = vi.fn(
    async () =>
      ({
        ok: true,
        status: 200,
        json: async () => ({}),
        text: async () => "{}",
      }) as Response,
  );
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("one click on a configured brain row opens it and switches nothing", () => {
  const { onActivateOptimistic } = renderTier([
    brainCard({ active: true }),
    brainCard({ id: "cloud-b", label: "Cloud B" }),
  ]);
  // The list opens on the active provider; the other row is collapsed.
  expect(screen.queryByTestId("provider-body-cloud-b")).toBeNull();

  fireEvent.click(screen.getByTestId("provider-row-cloud-b"));

  expect(screen.getByTestId("provider-body-cloud-b")).toBeTruthy();
  expect(onActivateOptimistic).not.toHaveBeenCalled();
  expect(switches()).toEqual([]);
});

it("a brain card has no Use control; only the brain that answers says so", () => {
  renderTier([brainCard({ active: true }), brainCard({ id: "cloud-b", label: "Cloud B" })]);
  expect(screen.queryByRole("radio")).toBeNull();
  expect(screen.getByTestId("provider-brain-state-cloud-a")).toBeTruthy();
  expect(screen.queryByTestId("provider-brain-state-cloud-b")).toBeNull();
});

it("a double click opens the row once and switches nothing", () => {
  renderTier([brainCard({ active: true }), brainCard({ id: "cloud-b", label: "Cloud B" })]);
  const row = screen.getByTestId("provider-row-cloud-b");

  // A real double click: click, click(detail 2), dblclick.
  fireEvent.click(row);
  fireEvent.click(row, { detail: 2 });
  fireEvent.doubleClick(row);

  expect(screen.getByTestId("provider-body-cloud-b")).toBeTruthy();
  expect(switches()).toEqual([]);
});

it("a click on a row WITHOUT a key only opens its key field — no switch, no warning", () => {
  const { onActivateOptimistic } = renderTier([
    brainCard({ active: true }),
    brainCard({ id: "cloud-b", label: "Cloud B", configured: false, secrets_set: {} }),
  ]);

  fireEvent.click(screen.getByTestId("provider-row-cloud-b"));

  expect(screen.getByTestId("provider-body-cloud-b")).toBeTruthy();
  expect(onActivateOptimistic).not.toHaveBeenCalled();
  expect(switches()).toEqual([]);
});

it("a click on the open active row closes it and switches nothing", () => {
  const { onActivateOptimistic } = renderTier([
    brainCard({ active: true }),
    brainCard({ id: "cloud-b", label: "Cloud B" }),
  ]);
  expect(screen.getByTestId("provider-body-cloud-a")).toBeTruthy();

  fireEvent.click(screen.getByTestId("provider-row-cloud-a"));

  expect(screen.queryByTestId("provider-body-cloud-a")).toBeNull();
  expect(onActivateOptimistic).not.toHaveBeenCalled();
  expect(switches()).toEqual([]);
});

it("Enter on a focused row opens it like a click", () => {
  renderTier([brainCard({ active: true }), brainCard({ id: "cloud-b", label: "Cloud B" })]);
  const row = screen.getByTestId("provider-row-cloud-b");

  fireEvent.keyDown(row, { key: "Enter" });

  expect(screen.getByTestId("provider-body-cloud-b")).toBeTruthy();
  expect(switches()).toEqual([]);
});

/** Model catalogues the open cards asked for, by provider id. */
function catalogues(): string[] {
  return fetchMock.mock.calls
    .map((c) => String(c[0]).match(/^\/api\/providers\/([^/]+)\/models/)?.[1])
    .filter((id): id is string => Boolean(id));
}

it("the Hermes card has no model picker: the brain picker sets Hermes's model", () => {
  renderTier([brainCard({ id: "hermes", label: "Hermes Agent", active: true })]);
  expect(screen.getByTestId("provider-body-hermes")).toBeTruthy();
  expect(catalogues()).toEqual([]);
});

it("another brain card keeps its model picker", async () => {
  renderTier([brainCard({ active: true })]);
  await vi.waitFor(() => expect(catalogues()).toContain("cloud-a"));
});
