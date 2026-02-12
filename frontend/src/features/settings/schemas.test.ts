import { describe, expect, it } from "vitest";

import {
  DashboardSettingsSchema,
  SettingsUpdateRequestSchema,
} from "@/features/settings/schemas";

describe("DashboardSettingsSchema", () => {
  it("parses settings payload", () => {
    const parsed = DashboardSettingsSchema.parse({
      stickyThreadsEnabled: true,
      preferEarlierResetAccounts: false,
      totpRequiredOnLogin: true,
      totpConfigured: false,
      apiKeyAuthEnabled: true,
    });

    expect(parsed.stickyThreadsEnabled).toBe(true);
    expect(parsed.apiKeyAuthEnabled).toBe(true);
  });
});

describe("SettingsUpdateRequestSchema", () => {
  it("accepts required fields and optional updates", () => {
    const parsed = SettingsUpdateRequestSchema.parse({
      stickyThreadsEnabled: false,
      preferEarlierResetAccounts: true,
      totpRequiredOnLogin: true,
      apiKeyAuthEnabled: false,
    });

    expect(parsed.totpRequiredOnLogin).toBe(true);
    expect(parsed.apiKeyAuthEnabled).toBe(false);
  });

  it("accepts payload without optional fields", () => {
    const parsed = SettingsUpdateRequestSchema.parse({
      stickyThreadsEnabled: false,
      preferEarlierResetAccounts: true,
    });

    expect(parsed.totpRequiredOnLogin).toBeUndefined();
    expect(parsed.apiKeyAuthEnabled).toBeUndefined();
  });

  it("rejects invalid types", () => {
    const result = SettingsUpdateRequestSchema.safeParse({
      stickyThreadsEnabled: "yes",
      preferEarlierResetAccounts: true,
    });

    expect(result.success).toBe(false);
  });
});
