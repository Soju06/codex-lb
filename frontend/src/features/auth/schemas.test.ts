import { describe, expect, it } from "vitest";

import {
  AuthSessionSchema,
  getFirstZodIssueMessage,
  GuestPasswordSetRequestSchema,
  LoginRequestSchema,
  PasswordChangeRequestSchema,
  PasswordSetupRequestSchema,
} from "@/features/auth/schemas";

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
      passwordSessionActive: false,
      role: "guest",
      permissions: [],
      guestAccessEnabled: false,
      guestPasswordRequired: false,
      user: null,
      authMethod: null,
      mustChangePassword: false,
      totpEnrollmentRequired: false,
      login: {
        usernameField: "hidden",
        providers: [{ kind: "password", providerKey: "default", label: "Password", loginUrl: null }],
        localLogin: "enabled",
        pendingIdentity: false,
      },
      accessSummary: null,
      assignableRoleIds: [],
      localPasswordConfigured: false,
      stepUp: null,
      breakGlassSession: false,
    });
  });

  it("falls back to the open policy rather than failing the whole session on an unknown value", () => {
    const parsed = AuthSessionSchema.parse({
      authenticated: true,
      passwordRequired: true,
      totpRequiredOnLogin: false,
      totpConfigured: true,
      login: { usernameField: "shown", providers: [], localLogin: "something_new", pendingIdentity: false },
      accessSummary: {
        usersTotal: 1,
        usersActive: 1,
        usersInvited: 0,
        usersDisabled: 0,
        pendingInvites: 0,
        nonAdminUsers: 0,
        customRoles: 0,
        providersEnabled: ["password"],
        roleMappings: 0,
        scimTokens: 0,
        auditSinks: 0,
        localLoginPolicy: "something_new",
      },
    });

    expect(parsed.login.localLogin).toBe("enabled");
    expect(parsed.accessSummary?.localLoginPolicy).toBe("enabled");
  });

  it("reports a break-glass session when the server marks one", () => {
    const parsed = AuthSessionSchema.parse({
      authenticated: true,
      passwordRequired: true,
      totpRequiredOnLogin: false,
      totpConfigured: true,
      breakGlassSession: true,
    });

    expect(parsed.breakGlassSession).toBe(true);
  });

  it("defaults the account fields for payloads that predate them", () => {
    const parsed = AuthSessionSchema.parse({
      authenticated: true,
      passwordRequired: true,
      totpRequiredOnLogin: false,
      totpConfigured: true,
      role: "admin",
      permissions: ["read", "write"],
    });

    expect(parsed.user).toBeNull();
    expect(parsed.login.usernameField).toBe("hidden");
    expect(parsed.accessSummary).toBeNull();
    expect(parsed.assignableRoleIds).toEqual([]);
    expect(parsed.totpEnrollmentRequired).toBe(false);
    expect(parsed.mustChangePassword).toBe(false);
  });

  it("parses the account block, login hint and access summary", () => {
    const parsed = AuthSessionSchema.parse({
      authenticated: true,
      passwordRequired: true,
      totpRequiredOnLogin: false,
      totpConfigured: true,
      role: "admin",
      permissions: ["read", "write", "users:manage:all"],
      user: { id: "u1", username: "admin", displayName: null, role: { id: "r1", slug: "admin", name: "Admin", kind: "preset" } },
      authMethod: "password",
      login: { usernameField: "shown", providers: [{ kind: "password", label: "Password" }], localLogin: "enabled" },
      accessSummary: {
        usersTotal: 2,
        usersActive: 2,
        usersInvited: 0,
        usersDisabled: 0,
        pendingInvites: 0,
        nonAdminUsers: 1,
        customRoles: 0,
        providersEnabled: ["password"],
        roleMappings: 0,
        scimTokens: 0,
        auditSinks: 0,
        localLoginPolicy: "enabled",
      },
      assignableRoleIds: ["r1"],
    });

    expect(parsed.user?.username).toBe("admin");
    expect(parsed.login.usernameField).toBe("shown");
    expect(parsed.login.providers[0]?.loginUrl).toBeNull();
    expect(parsed.accessSummary?.usersTotal).toBe(2);
    expect(parsed.assignableRoleIds).toEqual(["r1"]);
  });

  it("treats an explicit null login hint as the hidden default", () => {
    const parsed = AuthSessionSchema.parse({
      authenticated: false,
      passwordRequired: true,
      totpRequiredOnLogin: false,
      totpConfigured: false,
      login: null,
    });

    expect(parsed.login.usernameField).toBe("hidden");
  });

  it("defaults role and permissions to least privilege when omitted", () => {
    const parsed = AuthSessionSchema.parse({
      authenticated: true,
      passwordRequired: false,
      totpRequiredOnLogin: false,
      totpConfigured: false,
    });

    expect(parsed.role).toBe("guest");
    expect(parsed.permissions).toEqual([]);
  });

  it("accepts fine-grained permission strings alongside the coarse aliases", () => {
    const parsed = AuthSessionSchema.parse({
      authenticated: true,
      passwordRequired: true,
      totpRequiredOnLogin: false,
      totpConfigured: false,
      role: "admin",
      permissions: ["read", "write", "accounts:export", "security:write"],
    });

    expect(parsed.permissions).toEqual(["read", "write", "accounts:export", "security:write"]);
  });

  it("rejects unknown roles", () => {
    const result = AuthSessionSchema.safeParse({
      authenticated: true,
      passwordRequired: true,
      totpRequiredOnLogin: false,
      totpConfigured: false,
      role: "superuser",
    });

    expect(result.success).toBe(false);
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

  it("accepts an optional username", () => {
    const parsed = LoginRequestSchema.parse({ username: " alice ", password: "strong-password" });
    expect(parsed.username).toBe("alice");
  });

  it("rejects empty password", () => {
    expect(
      LoginRequestSchema.safeParse({
        password: "",
      }).success,
    ).toBe(false);
  });
});

describe("dashboard password length cap (#615)", () => {
  // Mirrors `_MAX_PASSWORD_BYTES = 72` in the backend
  // `app/modules/dashboard_auth/api.py`. Without these guards the form would
  // still post and the user would only learn about the failure when the
  // server returns HTTP 422 `password_too_long`.

  it("PasswordSetupRequestSchema accepts a password whose UTF-8 length is exactly 72 bytes", () => {
    const exact = "a".repeat(72);
    expect(new TextEncoder().encode(exact)).toHaveLength(72);
    expect(
      PasswordSetupRequestSchema.safeParse({
        password: exact,
      }).success,
    ).toBe(true);
  });

  it("PasswordSetupRequestSchema rejects an ASCII password whose UTF-8 length exceeds 72 bytes", () => {
    const tooLong = "a".repeat(73);
    expect(new TextEncoder().encode(tooLong)).toHaveLength(73);
    const result = PasswordSetupRequestSchema.safeParse({
      password: tooLong,
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0]?.message).toBe("settings.password.validation.maxByteLength");
    }
  });

  it("PasswordSetupRequestSchema rejects a multi-byte password whose UTF-8 length exceeds 72 bytes", () => {
    // Each `🔒` is 4 UTF-8 bytes; 19 of them = 76 bytes.
    const emojiPassword = "🔒".repeat(19);
    expect(new TextEncoder().encode(emojiPassword).length).toBe(76);
    const result = PasswordSetupRequestSchema.safeParse({
      password: emojiPassword,
    });
    expect(result.success).toBe(false);
  });

  it("PasswordSetupRequestSchema still rejects passwords below the 8-character minimum", () => {
    expect(
      PasswordSetupRequestSchema.safeParse({
        password: "short",
      }).success,
    ).toBe(false);
  });

  it("PasswordChangeRequestSchema enforces the 72-byte cap on newPassword", () => {
    const tooLong = "z".repeat(73);
    const result = PasswordChangeRequestSchema.safeParse({
      currentPassword: "current-password-x",
      newPassword: tooLong,
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      // Error must point at newPassword (not currentPassword) so the form
      // surfaces the violation on the right field.
      expect(result.error.issues[0]?.path).toContain("newPassword");
    }
  });

  it("PasswordChangeRequestSchema accepts a 72-byte newPassword", () => {
    expect(
      PasswordChangeRequestSchema.safeParse({
        currentPassword: "current-password-x",
        newPassword: "b".repeat(72),
      }).success,
    ).toBe(true);
  });

  it("exposes the first Zod issue message for imperative parse callers", () => {
    const result = GuestPasswordSetRequestSchema.safeParse({
      password: "short",
    });

    expect(result.success).toBe(false);
    if (!result.success) {
      expect(getFirstZodIssueMessage(result.error)).toBe("settings.password.validation.minLength");
    }
  });
});
