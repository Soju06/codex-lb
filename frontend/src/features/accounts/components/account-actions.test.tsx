import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AccountRoutingPolicyControl } from "@/features/accounts/components/account-routing-policy-control";
import { createAccountSummary } from "@/test/mocks/factories";

describe("AccountRoutingPolicyControl", () => {
  it("renders an explicit routing policy selector", async () => {
    const onRoutingPolicyChange = vi.fn();
    const account = createAccountSummary({ routingPolicy: "normal" });

    render(
      <AccountRoutingPolicyControl
        account={account}
        busy={false}
        onRoutingPolicyChange={onRoutingPolicyChange}
      />,
    );

    expect(screen.getByText("Routing policy")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Routing policy" })).toHaveTextContent("Normal");
  });
});
