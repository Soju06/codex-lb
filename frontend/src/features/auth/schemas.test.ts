import { describe, expect, it } from "vitest";

import { AuthSessionSchema, LoginRequestSchema } from "@/features/auth/schemas";

describe("AuthSessionSchema", () => {
  it("parses valid auth session payload", () => {
    const parsed = AuthSessionSchema.parse({
      authenticated: true,
      passwordRequired: true,
      totpRequiredOnLogin: false,
      totpConfigured: true,
      authMode: "trusted_header",
      passwordManagementEnabled: true,
    });

    expect(parsed).toEqual({
      authenticated: true,
      passwordRequired: true,
      totpRequiredOnLogin: false,
      totpConfigured: true,
      bootstrapRequired: false,
      bootstrapTokenConfigured: false,
      authMode: "trusted_header",
      passwordManagementEnabled: true,
    });
  });

  it("rejects missing required fields", () => {
    const result = AuthSessionSchema.safeParse({
      authenticated: true,
      passwordRequired: false,
      totpRequiredOnLogin: false,
    });

    expect(result.success).toBe(false);
  });

  it("defaults optional auth mode fields for older responses", () => {
    const parsed = AuthSessionSchema.parse({
      authenticated: true,
      passwordRequired: false,
      totpRequiredOnLogin: false,
      totpConfigured: false,
    });

    expect(parsed.bootstrapRequired).toBe(false);
    expect(parsed.bootstrapTokenConfigured).toBe(false);
    expect(parsed.authMode).toBe("standard");
    expect(parsed.passwordManagementEnabled).toBe(true);
  });
});

describe("LoginRequestSchema", () => {
  it("accepts non-empty password", () => {
    expect(
      LoginRequestSchema.safeParse({
        password: "strong-password",
      }).success,
    ).toBe(true);
  });

  it("rejects empty password", () => {
    expect(
      LoginRequestSchema.safeParse({
        password: "",
      }).success,
    ).toBe(false);
  });
});
