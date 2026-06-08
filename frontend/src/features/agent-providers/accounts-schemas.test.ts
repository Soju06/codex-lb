import { describe, expect, it } from "vitest";

import {
  AgentProviderAccountsSchema,
  AgentProviderAccountUpdateSchema,
  AntigravityProviderAccountCreateSchema,
} from "@/features/agent-providers/accounts-schemas";

describe("AgentProviderAccountsSchema", () => {
  it("parses provider-scoped account metadata without secret material", () => {
    const parsed = AgentProviderAccountsSchema.parse({
      accounts: [
        {
          accountId: "acct_1",
          providerId: "gemini",
          displayName: "Gemini dev key",
          status: "active",
          authMode: "api_key",
          apiKeySet: true,
          credentialFingerprint: "abc123",
          projectId: "dev-project",
          location: "global",
          createdAt: "2026-06-09T00:00:00Z",
          updatedAt: "2026-06-09T00:00:00Z",
        },
        {
          accountId: "agy_1",
          providerId: "antigravity",
          externalAccountId: "default",
          displayName: "Antigravity default",
          status: "configured",
          authMode: "cli_keyring",
          apiKeySet: false,
          credentialFingerprint: "fingerprint",
          projectId: "workspace-a",
          location: "agy",
          createdAt: "2026-06-09T00:00:00Z",
          updatedAt: "2026-06-09T00:00:00Z",
        },
      ],
    });

    expect(parsed.accounts[0].providerId).toBe("gemini");
    expect("apiKey" in parsed.accounts[0]).toBe(false);
    expect(parsed.accounts[1].providerId).toBe("antigravity");
    expect(parsed.accounts[1].apiKeySet).toBe(false);
  });

  it("parses provider account lifecycle update payloads", () => {
    const parsed = AgentProviderAccountUpdateSchema.parse({
      displayName: "Gemini prod",
      status: "paused",
      apiKey: "new-key",
      projectId: "prod-project",
      location: "global",
    });

    expect(parsed.status).toBe("paused");
    expect(parsed.apiKey).toBe("new-key");
  });

  it("parses Antigravity API-key account creation payloads", () => {
    const parsed = AntigravityProviderAccountCreateSchema.parse({
      displayName: "Antigravity managed",
      authMode: "api_key",
      apiKey: "AIza-secret",
      projectId: "agent-project",
    });

    expect(parsed.authMode).toBe("api_key");
    expect(parsed.apiKey).toBe("AIza-secret");
  });
});
