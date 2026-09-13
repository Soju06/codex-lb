import { describe, expect, it } from "vitest";

import {
  breakGlassDesignations,
  hasCompanyLogin,
  isLocalLoginRestricted,
  isOrganisationConfigured,
  isQualifyingBreakGlass,
  moveInOrder,
  normalizeEmailDomain,
  orderRolesForPicker,
  refusedSince,
  REFUSED_WINDOW_DAYS,
  rulesOf,
  suggestedEmailDomain,
  trustedHeaderProvider,
} from "@/features/organisation/rules";
import {
  createAccessSummary,
  createAuthProvider,
  createDashboardUser,
  createDashboardRole,
  createDefaultAuthProviders,
  createDefaultDashboardRoles,
  createDefaultRefusedSignIns,
  createRefusedSignIn,
  createRoleMapping,
  PRESET_ROLE_IDS,
} from "@/test/mocks/factories";

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
