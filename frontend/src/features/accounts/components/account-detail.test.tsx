import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AccountDetail } from "@/features/accounts/components/account-detail";
import { createAccountSummary } from "@/test/mocks/factories";

vi.mock("@/features/accounts/hooks/use-accounts", () => ({
  useAccountTrends: () => ({ data: undefined }),
}));

describe("AccountDetail", () => {
  it("hides the priority selector and badge when priorities are disabled", () => {
    render(
      <AccountDetail
        account={createAccountSummary({ priority: "gold" })}
        busy={false}
        prioritiesEnabled={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onDelete={vi.fn()}
        onReauth={vi.fn()}
        onPriorityChange={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.queryByText("Gold")).not.toBeInTheDocument();
    expect(screen.queryByText("Priority")).not.toBeInTheDocument();
  });
});
