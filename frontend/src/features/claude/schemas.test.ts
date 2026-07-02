import { describe, expect, it } from "vitest";

import {
  AddClaudeAccountRequestSchema,
  ClaudeAccountSchema,
  ClaudeAccountsResponseSchema,
  DisableClaudeAccountRequestSchema,
} from "@/features/claude/schemas";

const ISO = "2026-07-01T00:00:00+00:00";

describe("ClaudeAccountSchema", () => {
  it("parses a complete account payload", () => {
    const parsed = ClaudeAccountSchema.parse({
      id: "claude-abc",
      claudeAccountUuid: "abc-123",
      userEmail: "user@example.com",
      userOrganizationUuid: "org-1",
      status: "active",
      isActive: true,
      claudeAccessTokenExpiresAt: ISO,
      lastUsedAt: ISO,
      rateLimitRequestsRemaining: 42,
      rateLimitInputTokensRemaining: 1_000_000,
      rateLimitOutputTokensRemaining: 500_000,
      rateLimitStatus: "allowed",
      createdAt: ISO,
    });
    expect(parsed.id).toBe("claude-abc");
    expect(parsed.userEmail).toBe("user@example.com");
    expect(parsed.rateLimitRequestsRemaining).toBe(42);
    expect(parsed.isActive).toBe(true);
  });

  it("tolerates nullable fields", () => {
    const parsed = ClaudeAccountSchema.parse({
      id: "claude-abc",
      claudeAccountUuid: "abc-123",
      userEmail: null,
      userOrganizationUuid: null,
      status: null,
      isActive: false,
      claudeAccessTokenExpiresAt: null,
      lastUsedAt: null,
      rateLimitRequestsRemaining: null,
      rateLimitInputTokensRemaining: null,
      rateLimitOutputTokensRemaining: null,
      rateLimitStatus: null,
      createdAt: ISO,
    });
    expect(parsed.userEmail).toBeNull();
    expect(parsed.isActive).toBe(false);
  });

  it("rejects non-boolean isActive", () => {
    expect(() =>
      ClaudeAccountSchema.parse({
        id: "x",
        claudeAccountUuid: "y",
        isActive: "yes",
        createdAt: ISO,
      }),
    ).toThrow();
  });
});

describe("ClaudeAccountsResponseSchema", () => {
  it("wraps an accounts list", () => {
    const parsed = ClaudeAccountsResponseSchema.parse({
      accounts: [
        {
          id: "claude-1",
          claudeAccountUuid: "uuid-1",
          isActive: true,
          createdAt: ISO,
        },
      ],
    });
    expect(parsed.accounts).toHaveLength(1);
  });
});

describe("AddClaudeAccountRequestSchema", () => {
  it("accepts the minimal required payload", () => {
    const parsed = AddClaudeAccountRequestSchema.parse({
      claudeAccountUuid: "abc",
      accessToken: "AT",
      refreshToken: "RT",
      expiresInSeconds: 3600,
    });
    expect(parsed.expiresInSeconds).toBe(3600);
    expect(parsed.scopes).toBeUndefined();
  });

  it("accepts the full payload with optional fields", () => {
    const parsed = AddClaudeAccountRequestSchema.parse({
      claudeAccountUuid: "abc",
      accessToken: "AT",
      refreshToken: "RT",
      expiresInSeconds: 3600,
      scopes: ["user:profile", "user:inference"],
      userEmail: "user@example.com",
      userOrganizationUuid: "org-1",
    });
    expect(parsed.scopes).toEqual(["user:profile", "user:inference"]);
    expect(parsed.userEmail).toBe("user@example.com");
  });

  it("rejects empty token fields", () => {
    expect(() =>
      AddClaudeAccountRequestSchema.parse({
        claudeAccountUuid: "abc",
        accessToken: "",
        refreshToken: "RT",
        expiresInSeconds: 3600,
      }),
    ).toThrow();
  });

  it("rejects non-positive expiresInSeconds", () => {
    expect(() =>
      AddClaudeAccountRequestSchema.parse({
        claudeAccountUuid: "abc",
        accessToken: "AT",
        refreshToken: "RT",
        expiresInSeconds: 0,
      }),
    ).toThrow();
  });

  it("rejects malformed email", () => {
    expect(() =>
      AddClaudeAccountRequestSchema.parse({
        claudeAccountUuid: "abc",
        accessToken: "AT",
        refreshToken: "RT",
        expiresInSeconds: 3600,
        userEmail: "not-an-email",
      }),
    ).toThrow();
  });
});

describe("DisableClaudeAccountRequestSchema", () => {
  it("accepts an empty reason", () => {
    const parsed = DisableClaudeAccountRequestSchema.parse({});
    expect(parsed.reason).toBeUndefined();
  });

  it("accepts a reason string", () => {
    const parsed = DisableClaudeAccountRequestSchema.parse({ reason: "manual" });
    expect(parsed.reason).toBe("manual");
  });
});