import { describe, expect, it } from "vitest";

import {
  AgentProviderListSchema,
  AgentProviderOverviewSchema,
} from "@/features/agent-providers/schemas";

describe("AgentProviderListSchema", () => {
  it("parses the provider metadata contract", () => {
    const parsed = AgentProviderListSchema.parse({
      providers: [
        {
          providerId: "codex",
          displayName: "Codex",
          status: "ready",
          authModes: ["chatgpt_oauth"],
          quotaDimensions: ["primary", "secondary", "additional_quota"],
          dashboardSections: ["accounts", "settings"],
          capabilities: [
            {
              protocol: "codex_chatgpt",
              status: "ready",
              proxyable: true,
              streaming: true,
              lifecycleNotes: "Existing production surface.",
              operatorAction: "Keep current settings.",
              availableUntil: null,
              notes: "Existing Codex routes.",
            },
          ],
        },
        {
          providerId: "gemini",
          displayName: "Gemini",
          status: "foundation",
          authModes: ["api_key", "google_cloud_adc", "cli_keyring"],
          quotaDimensions: ["rpm", "tpm", "rpd"],
          dashboardSections: ["accounts", "settings"],
          capabilities: [
            {
              protocol: "gemini_api",
              status: "foundation",
              proxyable: true,
              streaming: true,
              lifecycleNotes: "Gemini API endpoints.",
              operatorAction: "Add API-key accounts.",
              availableUntil: null,
              notes: "Gemini API foundation.",
            },
            {
              protocol: "antigravity_cli",
              status: "planned",
              proxyable: false,
              streaming: false,
              lifecycleNotes: "Gemini CLI individual tier cutover.",
              operatorAction: "Build agy harness connector.",
              availableUntil: "2026-06-18",
              notes: "Harness connector.",
            },
          ],
        },
        {
          providerId: "antigravity",
          displayName: "Antigravity",
          status: "foundation",
          authModes: ["api_key", "cli_keyring"],
          quotaDimensions: ["requests", "sessions"],
          dashboardSections: ["accounts", "settings"],
          capabilities: [
            {
              protocol: "interactions_api",
              status: "foundation",
              proxyable: true,
              streaming: false,
              lifecycleNotes: "Managed agent.",
              operatorAction: "Route antigravity-preview models.",
              availableUntil: null,
              notes: "Interactions API.",
            },
            {
              protocol: "antigravity_cli",
              status: "foundation",
              proxyable: false,
              streaming: false,
              lifecycleNotes: "agy harness.",
              operatorAction: "Run agy --print harness probes.",
              availableUntil: null,
              notes: "Harness connector.",
            },
          ],
        },
      ],
    });

    expect(parsed.providers.map((provider) => provider.providerId)).toEqual(["codex", "gemini", "antigravity"]);
  });
});

describe("AgentProviderOverviewSchema", () => {
  it("parses the combined provider overview contract", () => {
    const parsed = AgentProviderOverviewSchema.parse({
      timeframe: "7d",
      providers: [
        {
          providerId: "codex",
          displayName: "Codex",
          status: "ready",
          accountCount: 2,
          activeAccountCount: 1,
          quotaWindowCount: 0,
          requestCount: 3,
          successCount: 2,
          errorCount: 1,
          inputTokens: 10,
          outputTokens: 20,
          cachedInputTokens: 4,
        },
      ],
      totals: {
        providerCount: 3,
        accountCount: 2,
        activeAccountCount: 1,
        quotaWindowCount: 0,
        requestCount: 3,
        successCount: 2,
        errorCount: 1,
        inputTokens: 10,
        outputTokens: 20,
        cachedInputTokens: 4,
      },
    });

    expect(parsed.totals.providerCount).toBe(3);
    expect(parsed.providers[0].providerId).toBe("codex");
  });
});
