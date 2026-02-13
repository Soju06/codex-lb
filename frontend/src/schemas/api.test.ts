import { describe, expect, it } from "vitest";

import {
  ApiErrorResponseSchema,
  DashboardApiErrorSchema,
  OpenAIApiErrorSchema,
} from "@/schemas/api";

describe("DashboardApiErrorSchema", () => {
  it("parses dashboard error payload", () => {
    const parsed = DashboardApiErrorSchema.parse({
      error: {
        code: "quota_exceeded",
        message: "quota exceeded",
      },
    });

    expect(parsed.error.code).toBe("quota_exceeded");
    expect(parsed.error.message).toBe("quota exceeded");
  });

  it("rejects missing required fields", () => {
    const result = DashboardApiErrorSchema.safeParse({
      error: { code: "upstream_error" },
    });

    expect(result.success).toBe(false);
  });
});

describe("OpenAIApiErrorSchema", () => {
  it("accepts optional fields", () => {
    const parsed = OpenAIApiErrorSchema.parse({
      error: {
        message: "invalid request",
        type: "invalid_request_error",
      },
    });

    expect(parsed.error.message).toBe("invalid request");
    expect(parsed.error.type).toBe("invalid_request_error");
  });
});

describe("ApiErrorResponseSchema", () => {
  it("accepts dashboard and openai shapes", () => {
    expect(
      ApiErrorResponseSchema.safeParse({
        error: { code: "timeout", message: "request timeout" },
      }).success,
    ).toBe(true);

    expect(
      ApiErrorResponseSchema.safeParse({
        error: { message: "request failed", param: "model" },
      }).success,
    ).toBe(true);
  });

  it("rejects invalid field types", () => {
    const result = ApiErrorResponseSchema.safeParse({
      error: { code: 123, message: "bad type" },
    });

    expect(result.success).toBe(false);
  });
});
