import { describe, expect, it } from "vitest";

import {
  AccountOpenCodeAuthExportResponseSchema,
  AccountSummarySchema,
  ImportStateSchema,
  OAuthStateSchema,
} from "@/features/accounts/schemas";

const ISO = "2026-01-01T00:00:00+00:00";

describe("AccountSummarySchema", () => {
  it("parses lightweight account payload", () => {
    const parsed = AccountSummarySchema.parse({
      accountId: "acc-1",
      email: "user@example.com",
      displayName: "User",
      planType: "pro",
      status: "active",
      usage: {
        primaryRemainingPercent: 85,
        secondaryRemainingPercent: null,
      },
      resetAtPrimary: ISO,
      resetAtSecondary: null,
      windowMinutesPrimary: null,
      windowMinutesSecondary: 10080,
      requestUsage: {
        requestCount: 3,
        totalTokens: 1500,
        cachedInputTokens: 1100,
        totalCostUsd: 0.02,
      },
      auth: {
        access: {
          expiresAt: ISO,
          state: "valid",
        },
        refresh: {
          state: "stored",
        },
        idToken: {
          state: "parsed",
        },
      },
    });

    expect(parsed.accountId).toBe("acc-1");
    expect(parsed.usage?.primaryRemainingPercent).toBe(85);
    expect(parsed.windowMinutesSecondary).toBe(10080);
    expect(parsed.requestUsage?.totalCostUsd).toBe(0.02);
  });
});

describe("AccountOpenCodeAuthExportResponseSchema", () => {
  it("parses stock OpenCode auth export payloads", () => {
    const parsed = AccountOpenCodeAuthExportResponseSchema.parse({
      filename: "opencode-auth-user.json",
      account: {
        accountId: "acc-1",
        chatgptAccountId: "chatgpt-acc-1",
        email: "user@example.com",
      },
      authJson: {
        openai: {
          type: "oauth",
          refresh: "refresh-token",
          access: "access-token",
          expires: 2_000_000_000_000,
          accountId: "chatgpt-acc-1",
        },
      },
    });

    expect(parsed.authJson.openai.type).toBe("oauth");
    expect(parsed.authJson.openai.expires).toBe(2_000_000_000_000);
  });
});

describe("OAuthStateSchema", () => {
  it("parses pending device flow state", () => {
    const parsed = OAuthStateSchema.parse({
      status: "pending",
      method: "device",
      authorizationUrl: null,
      callbackUrl: null,
      verificationUrl: "https://example.com/device",
      userCode: "ABCD-EFGH",
      deviceAuthId: "device-1",
      intervalSeconds: 5,
      expiresInSeconds: 300,
      errorMessage: null,
    });

    expect(parsed.status).toBe("pending");
    expect(parsed.method).toBe("device");
  });

  it("rejects invalid status", () => {
    const result = OAuthStateSchema.safeParse({
      status: "done",
      method: null,
      authorizationUrl: null,
      callbackUrl: null,
      verificationUrl: null,
      userCode: null,
      deviceAuthId: null,
      intervalSeconds: null,
      expiresInSeconds: null,
      errorMessage: null,
    });

    expect(result.success).toBe(false);
  });
});

describe("ImportStateSchema", () => {
  it("parses import states", () => {
    expect(
      ImportStateSchema.safeParse({
        status: "uploading",
        message: null,
      }).success,
    ).toBe(true);

    expect(
      ImportStateSchema.safeParse({
        status: "success",
        message: "Imported 1 account",
      }).success,
    ).toBe(true);
  });
});
