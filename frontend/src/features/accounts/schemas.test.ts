import { describe, expect, it } from "vitest";

import {
  AccountSummarySchema,
  ImportStateSchema,
  OAuthStateSchema,
  PlatformIdentityCreateRequestSchema,
  PlatformIdentityUpdateRequestSchema,
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

  it("parses provider-aware platform identity payload", () => {
    const parsed = AccountSummarySchema.parse({
      accountId: "platform-1",
      email: "Platform Key",
      displayName: "Platform Key",
      planType: "openai_platform",
      status: "active",
      providerKind: "openai_platform",
      routingSubjectId: "platform-1",
      label: "Platform Key",
      eligibleRouteFamilies: ["public_models_http", "public_responses_http"],
      lastValidatedAt: ISO,
      lastAuthFailureReason: null,
      organization: "org_test",
      project: "proj_test",
    });

    expect(parsed.providerKind).toBe("openai_platform");
    expect(parsed.eligibleRouteFamilies).toEqual([
      "public_models_http",
      "public_responses_http",
    ]);
    expect(parsed.organization).toBe("org_test");
  });
});

describe("PlatformIdentityCreateRequestSchema", () => {
  it("normalizes optional fields and allows zero enabled route families", () => {
    const parsed = PlatformIdentityCreateRequestSchema.parse({
      label: "Platform Key",
      apiKey: "sk-platform-test",
      organization: "  ",
      project: "proj_test",
      eligibleRouteFamilies: [],
    });

    expect(parsed.organization).toBeUndefined();
    expect(parsed.project).toBe("proj_test");
    expect(parsed.eligibleRouteFamilies).toEqual([]);
  });
});

describe("PlatformIdentityUpdateRequestSchema", () => {
  it("preserves explicit clears for optional fields", () => {
    const parsed = PlatformIdentityUpdateRequestSchema.parse({
      label: "Platform Key",
      organization: "  ",
      project: null,
      eligibleRouteFamilies: ["public_models_http"],
    });

    expect(parsed.label).toBe("Platform Key");
    expect(parsed.organization).toBeNull();
    expect(parsed.project).toBeNull();
    expect(parsed.eligibleRouteFamilies).toEqual(["public_models_http"]);
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
