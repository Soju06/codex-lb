import { describe, expect, it } from "vitest";

import {
  armedByTestLogin,
  breakGlassDesignations,
  companyLoginLabel,
  isConnected,
  hasCompanyLogin,
  isLocalLoginRestricted,
  isOrganisationConfigured,
  isQualifyingBreakGlass,
  maskEmail,
  moveInOrder,
  normalizeEmailDomain,
  oidcFieldFromParam,
  oidcProvider,
  orderRolesForPicker,
  refusedSince,
  REFUSED_WINDOW_DAYS,
  rulesOf,
  suggestedEmailDomain,
  trustedHeaderProvider,
  validateOidcDraft,
  type OidcDraft,
} from "@/features/organisation/rules";
import {
  createAccessSummary,
  createAuthProvider,
  createDashboardUser,
  createDashboardRole,
  createDefaultAuthProviders,
  createOidcAuthProvider,
  createDefaultDashboardRoles,
  createDefaultRefusedSignIns,
  createRefusedSignIn,
  createRoleMapping,
  PRESET_ROLE_IDS, LOCAL_SIGN_IN_PROVIDER } from "@/test/mocks/factories";

describe("isOrganisationConfigured", () => {
  it("stays unconfigured for a password-only install", () => {
    expect(isOrganisationConfigured(createAccessSummary())).toBe(false);
  });

  it("counts a non-password sign-in method or a single rule as configured", () => {
    expect(isOrganisationConfigured(createAccessSummary({ providersEnabled: ["password", "trusted_header"] }))).toBe(
      true,
    );
    expect(isOrganisationConfigured(createAccessSummary({ roleMappings: 1 }))).toBe(true);
  });

  it("counts a restricted password sign-in on its own, with no company login at all", () => {
    const summary = createAccessSummary({ localLoginPolicy: "break_glass_only" });
    expect(isOrganisationConfigured(summary)).toBe(true);
    expect(isLocalLoginRestricted(summary)).toBe(true);
    expect(hasCompanyLogin(summary)).toBe(false);
  });

  it("fails closed when the summary is withheld", () => {
    expect(isOrganisationConfigured(null)).toBe(false);
    expect(isLocalLoginRestricted(null)).toBe(false);
    expect(hasCompanyLogin(null)).toBe(false);
  });
});

describe("break-glass qualification", () => {
  it("needs all five facts: designated, active, admin preset, second factor, local password", () => {
    expect(isQualifyingBreakGlass(createDashboardUser())).toBe(true);
    expect(isQualifyingBreakGlass(createDashboardUser({ isBreakGlass: false }))).toBe(false);
    expect(isQualifyingBreakGlass(createDashboardUser({ status: "disabled" }))).toBe(false);
    expect(isQualifyingBreakGlass(createDashboardUser({ totpConfigured: false }))).toBe(false);
    // A proxy-provisioned admin cannot use the local form the designation is about.
    expect(isQualifyingBreakGlass(createDashboardUser({ hasPassword: false }))).toBe(false);
    expect(
      isQualifyingBreakGlass(
        createDashboardUser({
          role: { id: PRESET_ROLE_IDS.operator, slug: "operator", name: "Operator", kind: "preset" },
        }),
      ),
    ).toBe(false);
  });

  it("lists the designations whether or not they qualify, so the card can name one to enrol", () => {
    const designated = createDashboardUser({ totpConfigured: false });
    const ordinary = createDashboardUser({ id: "user_ops", username: "ops", isBreakGlass: false });
    expect(breakGlassDesignations([designated, ordinary]).map((user) => user.username)).toEqual(["admin"]);
    expect(breakGlassDesignations(undefined)).toEqual([]);
  });
});

describe("refusedSince", () => {
  it("is the start of the reported window", () => {
    const now = new Date("2026-01-31T12:00:00.000Z");
    expect(refusedSince(now)).toBe("2026-01-24T12:00:00.000Z");
    expect(REFUSED_WINDOW_DAYS).toBe(7);
  });
});

describe("orderRolesForPicker", () => {
  const custom = createDashboardRole({ id: "role_billing", slug: "billing", name: "Billing", kind: "custom", locked: false });

  it("lists the presets in the order people meet them", () => {
    const ordered = orderRolesForPicker(createDefaultDashboardRoles(), { customRoles: 0 });
    expect(ordered.map((role) => role.slug)).toEqual(["admin", "operator", "member", "viewer", "guest"]);
  });

  it("hides custom roles until the install has one", () => {
    const roles = [...createDefaultDashboardRoles(), custom];
    expect(orderRolesForPicker(roles, { customRoles: 0 }).map((role) => role.slug)).not.toContain("billing");
    expect(orderRolesForPicker(roles, { customRoles: 1 }).map((role) => role.slug)).toEqual([
      "admin",
      "operator",
      "member",
      "viewer",
      "guest",
      "billing",
    ]);
  });
});

describe("moveInOrder", () => {
  it("moves one entry and leaves the rest in order", () => {
    expect(moveInOrder(["a", "b", "c"], 2, 0)).toEqual(["c", "a", "b"]);
    expect(moveInOrder(["a", "b", "c"], 0, 1)).toEqual(["b", "a", "c"]);
  });

  it("is a no-op outside the list", () => {
    expect(moveInOrder(["a", "b"], 0, 5)).toEqual(["a", "b"]);
    expect(moveInOrder(["a", "b"], -1, 0)).toEqual(["a", "b"]);
    expect(moveInOrder(["a", "b"], 1, 1)).toEqual(["a", "b"]);
  });
});

describe("provider and rule selection", () => {
  it("finds the reverse-proxy row among the sign-in methods", () => {
    expect(trustedHeaderProvider(createDefaultAuthProviders())?.kind).toBe("trusted_header");
    expect(trustedHeaderProvider([])).toBeNull();
    expect(trustedHeaderProvider(undefined)).toBeNull();
  });

  it("keeps only the rules of that provider instance", () => {
    const provider = createAuthProvider();
    const mine = createRoleMapping();
    const other = createRoleMapping({ id: "mapping_other", providerKey: "second", claimValue: "other" });
    expect(rulesOf([mine, other], provider).map((rule) => rule.id)).toEqual([mine.id]);
    expect(rulesOf([mine], null)).toEqual([]);
  });
});

describe("quick-add suggestion", () => {
  it("strips a leading at-sign and case-folds", () => {
    expect(normalizeEmailDomain("  @Example.COM ")).toBe("example.com");
  });

  it("suggests the domain most of the refusals came from", () => {
    expect(suggestedEmailDomain(createDefaultRefusedSignIns())).toBe("example.com");
    expect(
      suggestedEmailDomain([
        createRefusedSignIn({ id: 3, details: { reason: "unknown_identity", email: "a@one.test" } }),
        createRefusedSignIn({ id: 4, details: { reason: "unknown_identity", email: "b@two.test" } }),
        createRefusedSignIn({ id: 5, details: { reason: "unknown_identity", email: "c@two.test" } }),
      ]),
    ).toBe("two.test");
  });

  it("suggests nothing when no refusal carried an address", () => {
    expect(suggestedEmailDomain([createRefusedSignIn({ details: { reason: "unknown_identity" } })])).toBe("");
    expect(suggestedEmailDomain(undefined)).toBe("");
  });
});

describe("preset ids stay the ones the pickers order by", () => {
  it("keeps admin first", () => {
    const [first] = orderRolesForPicker(createDefaultDashboardRoles(), { customRoles: 0 });
    expect(first.id).toBe(PRESET_ROLE_IDS.admin);
  });
});

describe("the company sign-in row", () => {
  const CALLBACK = "/api/dashboard-auth/oidc/callback";
  const VALID: OidcDraft = {
    issuer: "https://login.example.com",
    discoveryUrl: "",
    clientId: ["codex", "lb"].join("-"),
    clientSecret: ["value", "0000"].join("-"),
    redirectUri: `https://codex.example.com${CALLBACK}`,
    subjectClaim: "",
    emailClaim: "",
    nameClaim: "",
    groupsClaim: "",
  };

  it("finds the identity-provider row and ignores the others", () => {
    expect(oidcProvider(createDefaultAuthProviders())?.kind).toBe("oidc");
    expect(oidcProvider([createAuthProvider()])).toBeNull();
    expect(oidcProvider(undefined)).toBeNull();
  });

  it("treats an empty configuration as nothing to show, not as a history", () => {
    expect(isConnected(createOidcAuthProvider())).toBe(true);
    expect(isConnected(createOidcAuthProvider({ config: {} }))).toBe(false);
    expect(isConnected(null)).toBe(false);
  });

  it("arms only on a stamp that moved, never on one that was already there", () => {
    const earlier = "2026-02-01T00:00:00Z";
    const later = "2026-02-01T00:05:00Z";
    // The stored proof may be somebody else's; the API will not say whose.
    expect(armedByTestLogin(earlier, earlier)).toBe(false);
    expect(armedByTestLogin(later, earlier)).toBe(false);
    expect(armedByTestLogin(earlier, later)).toBe(true);
    // A connection write clears the stamp, so a pre-flight starts from nothing.
    expect(armedByTestLogin(null, later)).toBe(true);
    expect(armedByTestLogin(null, null)).toBe(false);
    expect(armedByTestLogin(earlier, null)).toBe(false);
  });

  it("names the company sign-in only when exactly one is active", () => {
    const password = LOCAL_SIGN_IN_PROVIDER;
    const okta = { kind: "oidc", label: "Okta" };
    expect(companyLoginLabel([password, okta])).toBe("Okta");
    expect(companyLoginLabel([password])).toBeNull();
    expect(companyLoginLabel([password, okta, { kind: "trusted_header", label: "Proxy" }])).toBeNull();
  });

  it("maps the server's field name onto the one the form shows", () => {
    expect(oidcFieldFromParam("discovery_url")).toBe("discoveryUrl");
    expect(oidcFieldFromParam("issuer")).toBe("issuer");
    expect(oidcFieldFromParam("groups_claim")).toBe("groupsClaim");
    expect(oidcFieldFromParam("config")).toBeNull();
    expect(oidcFieldFromParam("username")).toBeNull();
  });

  it("accepts a document the server would accept", () => {
    expect(validateOidcDraft(VALID, { callbackPath: CALLBACK })).toEqual({});
    expect(
      validateOidcDraft({ ...VALID, discoveryUrl: "https://login.example.com/.well-known/openid-configuration" }, {
        callbackPath: CALLBACK,
      }),
    ).toEqual({});
  });

  it("catches everything that would come back unattributable", () => {
    expect(validateOidcDraft({ ...VALID, issuer: "" }, { callbackPath: CALLBACK })).toEqual({ issuer: "required" });
    expect(validateOidcDraft({ ...VALID, issuer: "http://login.example.com" }, { callbackPath: CALLBACK })).toEqual({
      issuer: "https",
    });
    expect(validateOidcDraft({ ...VALID, issuer: "not a url" }, { callbackPath: CALLBACK })).toEqual({
      issuer: "https",
    });
    expect(validateOidcDraft({ ...VALID, clientSecret: "" }, { callbackPath: CALLBACK })).toEqual({
      clientSecret: "required",
    });
    expect(validateOidcDraft({ ...VALID, clientId: "c".repeat(257) }, { callbackPath: CALLBACK })).toEqual({
      clientId: "tooLong",
    });
    expect(
      validateOidcDraft({ ...VALID, redirectUri: "https://codex.example.com/elsewhere" }, { callbackPath: CALLBACK }),
    ).toEqual({ redirectUri: "callback" });
    expect(validateOidcDraft({ ...VALID, groupsClaim: "two words" }, { callbackPath: CALLBACK })).toEqual({
      groupsClaim: "whitespace",
    });
  });

  it("judges the value the request will carry, not the one still in the box", () => {
    // The wizard trims before it sends, so a field holding nothing but spaces
    // reaches the server as the empty string its request model refuses with no
    // `param` — the exact refusal this validator exists to keep off the wire.
    expect(validateOidcDraft({ ...VALID, clientId: "   " }, { callbackPath: CALLBACK })).toEqual({
      clientId: "required",
    });
    expect(validateOidcDraft({ ...VALID, issuer: " \t " }, { callbackPath: CALLBACK })).toEqual({
      issuer: "required",
    });
    // And the other way: padding the wizard strips on its own is not a refusal,
    // because the server is never shown it. A blank optional address means
    // "use the default" whether it was left empty or left a space.
    expect(
      validateOidcDraft(
        {
          ...VALID,
          issuer: ` ${VALID.issuer} `,
          discoveryUrl: "  ",
          redirectUri: ` ${VALID.redirectUri} `,
          groupsClaim: " roles ",
        },
        { callbackPath: CALLBACK },
      ),
    ).toEqual({});
    // The secret is the one field sent verbatim, so its spaces are its own.
    expect(validateOidcDraft({ ...VALID, clientSecret: " " }, { callbackPath: CALLBACK })).toEqual({});
  });
});

describe("maskEmail", () => {
  // The rule the server applies in `app/core/utils/masking.py`. The refused
  // person is handed this projection of their own address as a reference and
  // the sheet shows it beside the address itself, so the two are the same
  // string or the reference is unfindable.
  it("keeps the first character of the local part and the whole domain", () => {
    expect(maskEmail(["sarah", "example.com"].join("@"))).toBe("s***@example.com");
    expect(maskEmail(["a", "example.com"].join("@"))).toBe("a***@example.com");
  });

  it("matches the server on the shapes an identity provider can still assert", () => {
    expect(maskEmail("@example.com")).toBe("***@example.com");
    expect(maskEmail(["a", "b", "c.com"].join("@"))).toBe("a***@b@c.com");
    expect(maskEmail("no-domain")).toBe("n***");
    expect(maskEmail("")).toBe("***");
  });
});
