import { describe, expect, it } from "vitest";

import {
  accessTabFromHash,
  ORGANISATION_SETTINGS_RETURN_URL,
  shouldExpandAdvancedSettings,
  shouldExpandOrganisationSettings,
} from "@/features/settings/advanced-settings-deeplink";

describe("advanced settings deep links", () => {
  it("expands the group for ?advanced=1 and #firewall only", () => {
    expect(shouldExpandAdvancedSettings("?advanced=1", "")).toBe(true);
    expect(shouldExpandAdvancedSettings("", "#firewall")).toBe(true);
    expect(shouldExpandAdvancedSettings("", "#access")).toBe(false);
  });

  it("expands the Organisation group for its own anchors", () => {
    expect(shouldExpandOrganisationSettings("", "#organisation")).toBe(true);
    expect(shouldExpandOrganisationSettings("", "#organisation-refused")).toBe(true);
    expect(shouldExpandOrganisationSettings("", "#organisation-login-policy")).toBe(true);
    expect(shouldExpandOrganisationSettings("", "#oidc")).toBe(true);
    expect(shouldExpandOrganisationSettings("", "#firewall")).toBe(false);
    expect(shouldExpandOrganisationSettings("", "")).toBe(false);
  });

  it("honours the destination the sign-in flow returns a browser to", () => {
    // The server fixed this URL; the frontend answers to it or the round trip
    // lands on a collapsed group with the wizard shut.
    const [search, hash] = ORGANISATION_SETTINGS_RETURN_URL.split("/settings")[1].split("#");
    expect(shouldExpandOrganisationSettings(search, `#${hash}`)).toBe(true);
    expect(shouldExpandOrganisationSettings("?org=1", "")).toBe(true);
    expect(shouldExpandOrganisationSettings("?org=0", "")).toBe(false);
    expect(shouldExpandOrganisationSettings("?advanced=1", "")).toBe(false);
  });

  it("maps the Access card hashes to tabs", () => {
    expect(accessTabFromHash("#access-people")).toBe("people");
    expect(accessTabFromHash("#access")).toBe("my-sign-in");
    expect(accessTabFromHash("#totp")).toBe("my-sign-in");
    expect(accessTabFromHash("#firewall")).toBeNull();
    expect(accessTabFromHash("")).toBeNull();
  });
});
