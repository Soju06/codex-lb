import { describe, expect, it } from "vitest";

import { shouldExpandAdvancedSettings } from "@/features/settings/advanced-settings-deeplink";

describe("shouldExpandAdvancedSettings", () => {
  it("stays collapsed for a plain settings URL", () => {
    expect(shouldExpandAdvancedSettings("", "")).toBe(false);
    expect(shouldExpandAdvancedSettings("?view=guest", "")).toBe(false);
  });

  it("opens for the advanced query or firewall hash", () => {
    expect(shouldExpandAdvancedSettings("?advanced=1", "")).toBe(true);
    expect(shouldExpandAdvancedSettings("", "#firewall")).toBe(true);
    expect(shouldExpandAdvancedSettings("?advanced=1", "#firewall")).toBe(true);
  });
});
