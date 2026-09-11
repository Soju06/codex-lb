import { describe, expect, it } from "vitest";

import { accessTabFromHash, shouldExpandAdvancedSettings } from "@/features/settings/advanced-settings-deeplink";

describe("advanced settings deep links", () => {
  it("expands the group for ?advanced=1 and #firewall only", () => {
    expect(shouldExpandAdvancedSettings("?advanced=1", "")).toBe(true);
    expect(shouldExpandAdvancedSettings("", "#firewall")).toBe(true);
    expect(shouldExpandAdvancedSettings("", "#access")).toBe(false);
  });

  it("maps the Access card hashes to tabs", () => {
    expect(accessTabFromHash("#access-people")).toBe("people");
    expect(accessTabFromHash("#access")).toBe("my-sign-in");
    expect(accessTabFromHash("#totp")).toBe("my-sign-in");
    expect(accessTabFromHash("#firewall")).toBeNull();
    expect(accessTabFromHash("")).toBeNull();
  });
});
