import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AccountActions } from "@/features/accounts/components/account-actions";
import { createAccountSummary } from "@/test/mocks/factories";

describe("AccountActions", () => {
  it("renders an explicit routing policy selector", async () => {
    const onRoutingPolicyChange = vi.fn();
    const account = createAccountSummary({ routingPolicy: "normal" });

    render(
      <AccountActions
        account={account}
        busy={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onDelete={vi.fn()}
        onReauth={vi.fn()}
        onRoutingPolicyChange={onRoutingPolicyChange}
      />,
    );

    expect(screen.getByText("Routing policy")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Routing policy" })).toHaveTextContent("Normal");
  });
});
