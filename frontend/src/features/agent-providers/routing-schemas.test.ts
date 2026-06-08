import { describe, expect, it } from "vitest";

import {
  AgentProviderPreflightSchema,
  AgentProviderRoutingSettingsSchema,
  AgentProviderRoutingSettingsUpdateSchema,
} from "@/features/agent-providers/routing-schemas";

describe("agent provider routing schemas", () => {
  it("parses provider routing settings", () => {
    const parsed = AgentProviderRoutingSettingsSchema.parse({
      providerId: "gemini",
      strategy: "ordered_fallback",
      singleAccountId: null,
      orderedAccountIds: ["gemini-2", "gemini-1"],
      quotaThresholdPct: 95,
      roundRobinCursor: null,
      createdAt: "2026-06-09T00:00:00Z",
      updatedAt: "2026-06-09T00:00:00Z",
    });

    expect(parsed.strategy).toBe("ordered_fallback");
    expect(parsed.orderedAccountIds).toEqual(["gemini-2", "gemini-1"]);
    expect(parsed.quotaThresholdPct).toBe(95);
  });

  it("parses preflight with account quota windows", () => {
    const parsed = AgentProviderPreflightSchema.parse({
      providerId: "gemini",
      selectedAccountId: "acc_1",
      deniedReason: null,
      candidateAccountIds: ["acc_1"],
      settings: {
        providerId: "gemini",
        strategy: "capacity_weighted",
        singleAccountId: null,
        orderedAccountIds: [],
        quotaThresholdPct: 100,
        roundRobinCursor: null,
        createdAt: "2026-06-09T00:00:00Z",
        updatedAt: "2026-06-09T00:00:00Z",
      },
      accounts: [
        {
          accountId: "acc_1",
          displayName: "Gemini dev",
          status: "active",
          quotaWindows: [
            {
              dimension: "requests_per_day",
              used: 20,
              limit: 100,
              resetAt: "2026-06-10T00:00:00Z",
              recordedAt: "2026-06-09T00:00:00Z",
            },
          ],
        },
      ],
    });

    expect(parsed.accounts[0]?.quotaWindows[0]?.dimension).toBe("requests_per_day");
  });

  it("parses ordered fallback updates", () => {
    const parsed = AgentProviderRoutingSettingsUpdateSchema.parse({
      strategy: "ordered_fallback",
      orderedAccountIds: ["gemini-2", "gemini-1"],
    });

    expect(parsed.orderedAccountIds).toEqual(["gemini-2", "gemini-1"]);
  });
});
