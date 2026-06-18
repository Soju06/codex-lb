import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AccountSummaryLine } from "@/features/dashboard/components/account-summary-line";
import { createAccountSummary } from "@/test/mocks/factories";

describe("AccountSummaryLine", () => {
  it("renders registered, active, and unavailable counts for mixed statuses", () => {
    render(
      <AccountSummaryLine
        accounts={[
          createAccountSummary({ accountId: "acc-1", status: "active" }),
          createAccountSummary({ accountId: "acc-2", status: "paused" }),
          createAccountSummary({ accountId: "acc-3", status: "rate_limited" }),
        ]}
      />,
    );

    expect(screen.getByTestId("dashboard-account-summary-line")).toHaveTextContent(
      /3.*registered.*1.*active.*2.*unavailable/,
    );
  });

  it("shows zero unavailable when all accounts are active", () => {
    render(
      <AccountSummaryLine
        accounts={[
          createAccountSummary({ accountId: "acc-1", status: "active" }),
          createAccountSummary({ accountId: "acc-2", status: "active" }),
        ]}
      />,
    );

    expect(screen.getByTestId("dashboard-account-summary-line")).toHaveTextContent(
      /2.*registered.*2.*active.*0.*unavailable/,
    );
  });

  it("renders zero counts for an empty dashboard account list", () => {
    render(<AccountSummaryLine accounts={[]} />);

    expect(screen.getByTestId("dashboard-account-summary-line")).toHaveTextContent(
      /0.*registered.*0.*active.*0.*unavailable/,
    );
  });
});
