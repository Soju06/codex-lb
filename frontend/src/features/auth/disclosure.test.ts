import { describe, expect, it } from "vitest";

import { resolveDisclosureTier } from "@/features/auth/disclosure";
import { AccessSummarySchema, type AccessSummary } from "@/features/auth/schemas";
import { createSessionUser } from "@/test/mocks/factories";

// A single active admin, no invites, nothing enterprise: the individual install.
const SOLO: AccessSummary = AccessSummarySchema.parse({
  usersTotal: 1,
  usersActive: 1,
  providersEnabled: ["password"],
});

describe("resolveDisclosureTier", () => {
  it("treats a missing summary without an account (guest, visitor, implicit admin) as individual", () => {
    expect(resolveDisclosureTier(null, null)).toBe("individual");
  });

  it("treats a missing summary with a signed-in account (no users:manage) as team", () => {
    expect(resolveDisclosureTier(null, createSessionUser({ username: "ops" }))).toBe("team");
  });

  it("keeps a single account with nothing else configured on the individual tier", () => {
    expect(resolveDisclosureTier(SOLO, createSessionUser())).toBe("individual");
    expect(resolveDisclosureTier(AccessSummarySchema.parse({}), null)).toBe("individual");
  });

  it.each<[string, Partial<AccessSummary>]>([
    ["a second account row (any status)", { usersTotal: 2, usersDisabled: 1 }],
    ["a pending invite", { pendingInvites: 1 }],
    ["a non-admin account", { nonAdminUsers: 1 }],
  ])("moves to team when %s exists", (_label, patch) => {
    expect(resolveDisclosureTier({ ...SOLO, ...patch }, createSessionUser())).toBe("team");
  });

  it.each<[string, Partial<AccessSummary>]>([
    ["a non-password provider", { providersEnabled: ["password", "trusted_header"] }],
    ["a custom role", { customRoles: 1 }],
    ["a SCIM token", { scimTokens: 1 }],
    ["an audit sink", { auditSinks: 1 }],
    ["a non-default local login policy", { localLoginPolicy: "break_glass_only" }],
    ["a role mapping", { roleMappings: 1 }],
  ])("moves to enterprise when %s exists", (_label, patch) => {
    expect(resolveDisclosureTier({ ...SOLO, ...patch }, createSessionUser())).toBe("enterprise");
  });

  it("lets enterprise facts win over team facts", () => {
    expect(resolveDisclosureTier({ ...SOLO, usersTotal: 5, customRoles: 1 }, null)).toBe("enterprise");
  });

  it("returns to individual when the extra rows are gone", () => {
    expect(resolveDisclosureTier({ ...SOLO, usersTotal: 2 }, createSessionUser())).toBe("team");
    expect(resolveDisclosureTier({ ...SOLO, usersTotal: 1 }, createSessionUser())).toBe("individual");
  });
});
