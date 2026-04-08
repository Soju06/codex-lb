import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PlatformIdentityPanel } from "@/features/accounts/components/platform-identity-panel";
import { createAccountSummary } from "@/test/mocks/factories";

describe("PlatformIdentityPanel", () => {
  it("describes fallback-only and stateless responses scope", () => {
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
          eligibleRouteFamilies: ["public_models_http", "public_responses_http"],
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
    expect(screen.getByText(/Fallback only\./)).toBeInTheDocument();
    expect(
      screen.getByText(/Stateless HTTP/, {
        exact: false,
      }),
    ).toBeInTheDocument();
  });
});
