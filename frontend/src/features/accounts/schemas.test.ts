import { describe, expect, it } from "vitest";

import {
  AccountSummarySchema,
  CredentialsImportResponseSchema,
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

describe("CredentialsImportResponseSchema", () => {
  it("parses credentials import summary payload", () => {
    const parsed = CredentialsImportResponseSchema.parse({
      total: 2,
      imported: 1,
      failed: 1,
      results: [
        {
          line: 1,
          email: "alpha@example.com",
          status: "imported",
          accountId: "acc_alpha",
          error: null,
        },
        {
          line: 2,
          email: "beta@example.com",
          status: "failed",
          accountId: null,
          error: "invalid otp",
        },
      ],
    });

    expect(parsed.total).toBe(2);
    expect(parsed.results[0]?.status).toBe("imported");
    expect(parsed.results[1]?.status).toBe("failed");
  });
});
