import { describe, expect, it } from "vitest";

import { providerFamily } from "@/components/providers/ProviderLogo";

describe("providerFamily", () => {
  it("draws the Nous Research mark on the Nous Portal card", () => {
    expect(providerFamily("nous")).toBe("nous");
  });

  it("does not claim an id that merely contains the word", () => {
    expect(providerFamily("autonomous")).toBeNull();
  });
});
