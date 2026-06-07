import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AccountProxyBinding } from "@/features/accounts/components/account-proxy-binding";
import { createAccountSummary, createUpstreamProxyAdmin } from "@/test/mocks/factories";

describe("AccountProxyBinding", () => {
  it("saves a selected account proxy pool binding", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const account = createAccountSummary({ accountId: "acc_primary" });
    const admin = createUpstreamProxyAdmin();

    render(<AccountProxyBinding account={account} admin={admin} busy={false} onSave={onSave} />);

    await user.click(screen.getByRole("button", { name: "Save binding" }));

    expect(onSave).toHaveBeenCalledWith("acc_primary", {
      poolId: "pool_primary",
      isActive: true,
    });
  });

  it("can disable an existing binding", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const account = createAccountSummary({ accountId: "acc_primary" });
    const admin = createUpstreamProxyAdmin({
      bindings: [{ accountId: "acc_primary", poolId: "pool_primary", isActive: true }],
    });

    render(<AccountProxyBinding account={account} admin={admin} busy={false} onSave={onSave} />);

    await user.click(screen.getByRole("switch", { name: "Enable account proxy binding" }));

    expect(onSave).toHaveBeenCalledWith("acc_primary", {
      poolId: "pool_primary",
      isActive: false,
    });
  });
});
