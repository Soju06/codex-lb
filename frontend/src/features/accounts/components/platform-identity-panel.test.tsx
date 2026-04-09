import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PlatformIdentityPanel } from "@/features/accounts/components/platform-identity-panel";
import { createAccountSummary } from "@/test/mocks/factories";

describe("PlatformIdentityPanel", () => {
  it("describes fallback-only public and backend codex HTTP scope", () => {
    render(
      <PlatformIdentityPanel
        account={createAccountSummary({
          accountId: "platform_1",
          email: "Platform Key",
          displayName: "Platform Key",
          label: "Platform Key",
          planType: "openai_platform",
          providerKind: "openai_platform",
          routingSubjectId: "platform_1",
          organization: "org_test",
          project: "proj_test",
          eligibleRouteFamilies: ["public_models_http", "public_responses_http", "backend_codex_http"],
          usage: null,
          auth: null,
          lastValidatedAt: null,
          lastAuthFailureReason: null,
        })}
      />,
    );

    expect(screen.getByText("Eligible fallback routes")).toBeInTheDocument();
    expect(screen.getByText(/Fallback HTTP \/v1\/models/)).toBeInTheDocument();
    expect(screen.getByText(/Fallback stateless HTTP \/v1\/responses/)).toBeInTheDocument();
    expect(
      screen.getByText(/Fallback HTTP \/backend-api\/codex\/models \+ stateless HTTP \/backend-api\/codex\/responses/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Fallback only\./)).toBeInTheDocument();
    expect(
      screen.getByText(/Public Responses fallback covers stateless HTTP/, {
        exact: false,
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Codex HTTP fallback covers/, {
        exact: false,
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Compact, websocket, and continuity-bound requests stay on ChatGPT\./, {
        exact: false,
      }),
    ).toBeInTheDocument();
  });
});
