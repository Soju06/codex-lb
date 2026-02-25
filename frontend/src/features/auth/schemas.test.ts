import { describe, expect, it } from "vitest";

import { AuthSessionSchema, LoginRequestSchema } from "@/features/auth/schemas";

describe("AuthSessionSchema", () => {
  it("parses valid auth session payload", () => {
    const parsed = AuthSessionSchema.parse({
      authenticated: true,
      passwordRequired: true,
      totpRequiredOnLogin: false,
      totpConfigured: true,
      user: {
        id: "dashboard-user-admin-default",
        username: "admin",
        role: "admin",
      },
    });

    expect(parsed).toEqual({
      authenticated: true,
      passwordRequired: true,
      totpRequiredOnLogin: false,
      totpConfigured: true,
      user: {
        id: "dashboard-user-admin-default",
        username: "admin",
        role: "admin",
      },
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
});

describe("LoginRequestSchema", () => {
  it("accepts non-empty username and password", () => {
    expect(
      LoginRequestSchema.safeParse({
        username: "admin",
        password: "strong-password",
      }).success,
    ).toBe(true);
  });

  it("rejects empty password", () => {
    expect(
      LoginRequestSchema.safeParse({
        username: "admin",
        password: "",
      }).success,
    ).toBe(false);
  });

  it("rejects empty username", () => {
    expect(
      LoginRequestSchema.safeParse({
        username: "",
        password: "strong-password",
      }).success,
    ).toBe(false);
  });
});
