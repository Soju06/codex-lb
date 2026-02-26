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
      routingStrategy: "round_robin",
      globalModelForceEnabled: true,
      globalModelForceModel: "gpt-5.3-codex",
      globalModelForceReasoningEffort: "normal",
      importWithoutOverwrite: true,
      totpRequiredOnLogin: true,
      totpConfigured: false,
      apiKeyAuthEnabled: true,
    });

    expect(parsed.stickyThreadsEnabled).toBe(true);
    expect(parsed.routingStrategy).toBe("round_robin");
    expect(parsed.globalModelForceEnabled).toBe(true);
    expect(parsed.globalModelForceModel).toBe("gpt-5.3-codex");
    expect(parsed.globalModelForceReasoningEffort).toBe("normal");
    expect(parsed.importWithoutOverwrite).toBe(true);
    expect(parsed.apiKeyAuthEnabled).toBe(true);
  });
});

describe("SettingsUpdateRequestSchema", () => {
  it("accepts required fields and optional updates", () => {
    const parsed = SettingsUpdateRequestSchema.parse({
      stickyThreadsEnabled: false,
      preferEarlierResetAccounts: true,
      routingStrategy: "usage_weighted",
      globalModelForceEnabled: true,
      globalModelForceModel: "gpt-5.3-codex",
      globalModelForceReasoningEffort: "xhigh",
      importWithoutOverwrite: true,
      totpRequiredOnLogin: true,
      apiKeyAuthEnabled: false,
    });

    expect(parsed.globalModelForceEnabled).toBe(true);
    expect(parsed.globalModelForceModel).toBe("gpt-5.3-codex");
    expect(parsed.globalModelForceReasoningEffort).toBe("xhigh");
    expect(parsed.importWithoutOverwrite).toBe(true);
    expect(parsed.routingStrategy).toBe("usage_weighted");
    expect(parsed.totpRequiredOnLogin).toBe(true);
    expect(parsed.apiKeyAuthEnabled).toBe(false);
  });

  it("accepts payload without optional fields", () => {
    const parsed = SettingsUpdateRequestSchema.parse({
      stickyThreadsEnabled: false,
      preferEarlierResetAccounts: true,
    });

    expect(parsed.globalModelForceEnabled).toBeUndefined();
    expect(parsed.globalModelForceModel).toBeUndefined();
    expect(parsed.globalModelForceReasoningEffort).toBeUndefined();
    expect(parsed.importWithoutOverwrite).toBeUndefined();
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
