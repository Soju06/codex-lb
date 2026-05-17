import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { AccountsPage } from "@/features/accounts/components/accounts-page";
import { useAccountQuotaDisplayStore } from "@/hooks/use-account-quota-display";
import type { AccountSummary } from "@/features/accounts/schemas";

vi.mock("@/features/accounts/hooks/use-accounts", () => ({
  useAccounts: vi.fn(),
  useAccountTrends: vi.fn(() => ({ data: null })),
}));

vi.mock("@/features/accounts/hooks/use-oauth", () => ({
  useOauth: vi.fn(() => ({
    state: {
      status: "idle",
      method: null,
      authorizationUrl: null,
      callbackUrl: null,
      verificationUrl: null,
      userCode: null,
      deviceAuthId: null,
      intervalSeconds: null,
      expiresInSeconds: null,
      errorMessage: null,
    },
    start: vi.fn(),
    complete: vi.fn(),
    manualCallback: vi.fn(),
    reset: vi.fn(),
  })),
}));

const { useAccounts } = await import("@/features/accounts/hooks/use-accounts");
const mockedUseAccounts = useAccounts as unknown as ReturnType<typeof vi.fn>;

function idleMutation() {
  return {
    isPending: false,
    error: null,
    mutateAsync: vi.fn(),
  };
}

function account(overrides: Partial<AccountSummary>): AccountSummary {
  return {
    accountId: "acc-default",
    email: "default@example.com",
    displayName: "Default",
    planType: "plus",
    status: "active",
    additionalQuotas: [],
    ...overrides,
  };
}

describe("AccountsPage", () => {
  beforeEach(() => {
    useAccountQuotaDisplayStore.setState({ quotaDisplay: "weekly" });
    vi.spyOn(Date, "now").mockReturnValue(new Date("2026-01-01T12:00:00.000Z").getTime());
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("defaults the selected account to the first account after display sorting", () => {
    mockedUseAccounts.mockReturnValue({
      accountsQuery: {
        data: [
          account({
            accountId: "acc-api-first",
            email: "api-first@example.com",
            displayName: "API First",
            resetAtSecondary: "2026-01-01T13:00:00.000Z",
            windowMinutesSecondary: 10_080,
          }),
          account({
            accountId: "acc-visible-first",
            email: "visible-first@example.com",
            displayName: "Visible First",
            resetAtSecondary: "2026-01-01T12:10:00.000Z",
            windowMinutesSecondary: 10_080,
          }),
        ],
        error: null,
        refetch: vi.fn(),
      },
      importMutation: idleMutation(),
      pauseMutation: idleMutation(),
      resumeMutation: idleMutation(),
      deleteMutation: idleMutation(),
      updateMutation: idleMutation(),
    } as unknown as ReturnType<typeof useAccounts>);

    render(
      <MemoryRouter>
        <AccountsPage />
      </MemoryRouter>,
    );

    expect(screen.getAllByText(/^(Visible First|API First)$/).map((el) => el.textContent)).toEqual([
      "Visible First",
      "API First",
      "Visible First",
    ]);
    expect(screen.getByRole("heading", { name: "Visible First" })).toBeInTheDocument();
  });
});
