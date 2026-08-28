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

  it("tests the selected proxy pool's first endpoint", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onTestEndpoint = vi.fn().mockResolvedValue({
      endpointId: "ep_primary",
      ok: true,
      statusCode: 200,
      elapsedMs: 37,
      error: null,
    });
    const account = createAccountSummary({ accountId: "acc_primary" });
    const admin = createUpstreamProxyAdmin();

    render(
      <AccountProxyBinding
        account={account}
        admin={admin}
        busy={false}
        onSave={onSave}
        onTestEndpoint={onTestEndpoint}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Test pool" }));

    expect(onTestEndpoint).toHaveBeenCalledWith("ep_primary");
    expect(await screen.findByText(/Connection ok/)).toBeInTheDocument();
    expect(screen.getByText(/HTTP 200/)).toBeInTheDocument();
  });

  it("selects the first proxy pool when admin data loads after mount", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onTestEndpoint = vi.fn().mockResolvedValue({
      endpointId: "ep_primary",
      ok: true,
      statusCode: 200,
      elapsedMs: 37,
      error: null,
    });
    const account = createAccountSummary({ accountId: "acc_primary" });
    const admin = createUpstreamProxyAdmin();

    const { rerender } = render(
      <AccountProxyBinding
        account={account}
        admin={null}
        busy={false}
        onSave={onSave}
        onTestEndpoint={onTestEndpoint}
      />,
    );

    expect(screen.queryByRole("button", { name: "Test pool" })).not.toBeInTheDocument();

    rerender(
      <AccountProxyBinding
        account={account}
        admin={admin}
        busy={false}
        onSave={onSave}
        onTestEndpoint={onTestEndpoint}
      />,
    );

    await user.click(await screen.findByRole("button", { name: "Test pool" }));

    expect(onTestEndpoint).toHaveBeenCalledWith("ep_primary");
  });

  it("hides a pool test result after selecting another pool", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onTestEndpoint = vi.fn().mockResolvedValue({
      endpointId: "ep_primary",
      ok: true,
      statusCode: 200,
      elapsedMs: 37,
      error: null,
    });
    const account = createAccountSummary({ accountId: "acc_primary" });
    const admin = createUpstreamProxyAdmin({
      endpoints: [
        {
          id: "ep_primary",
          name: "Primary proxy",
          scheme: "http",
          host: "proxy-primary.test",
          port: 8080,
          username: "operator",
          isActive: true,
        },
        {
          id: "ep_secondary",
          name: "Secondary proxy",
          scheme: "http",
          host: "proxy-secondary.test",
          port: 8081,
          username: null,
          isActive: true,
        },
      ],
      pools: [
        {
          id: "pool_primary",
          name: "Primary pool",
          isActive: true,
          endpointIds: ["ep_primary"],
        },
        {
          id: "pool_secondary",
          name: "Secondary pool",
          isActive: true,
          endpointIds: ["ep_secondary"],
        },
      ],
    });

    render(
      <AccountProxyBinding
        account={account}
        admin={admin}
        busy={false}
        onSave={onSave}
        onTestEndpoint={onTestEndpoint}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Test pool" }));
    expect(await screen.findByText(/Connection ok/)).toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: "Account proxy pool" }));
    await user.click(screen.getByRole("option", { name: "Secondary pool" }));

    expect(screen.queryByText(/Connection ok/)).not.toBeInTheDocument();
  });

  it("replaces a stale pool test result when a later test rejects", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onTestEndpoint = vi
      .fn()
      .mockResolvedValueOnce({
        endpointId: "ep_primary",
        ok: true,
        statusCode: 200,
        elapsedMs: 37,
        error: null,
      })
      .mockRejectedValueOnce(new Error("write access lost"));
    const account = createAccountSummary({ accountId: "acc_primary" });
    const admin = createUpstreamProxyAdmin();

    render(
      <AccountProxyBinding
        account={account}
        admin={admin}
        busy={false}
        onSave={onSave}
        onTestEndpoint={onTestEndpoint}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Test pool" }));
    expect(await screen.findByText(/Connection ok/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Test pool" }));

    expect(await screen.findByText(/Connection failed/)).toBeInTheDocument();
    expect(screen.getByText(/write access lost/)).toBeInTheDocument();
    expect(screen.queryByText(/Connection ok/)).not.toBeInTheDocument();
  });

  it("disables the pool test when the selected pool has no endpoints", () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onTestEndpoint = vi.fn().mockResolvedValue(undefined);
    const account = createAccountSummary({ accountId: "acc_primary" });
    const admin = createUpstreamProxyAdmin({
      pools: [{ id: "pool_empty", name: "Empty pool", isActive: true, endpointIds: [] }],
    });

    render(
      <AccountProxyBinding
        account={account}
        admin={admin}
        busy={false}
        onSave={onSave}
        onTestEndpoint={onTestEndpoint}
      />,
    );

    expect(screen.getByRole("button", { name: "Test pool" })).toBeDisabled();
  });

  it("disables account proxy controls for read-only guests", () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const account = createAccountSummary({ accountId: "acc_primary" });
    const admin = createUpstreamProxyAdmin({
      bindings: [{ accountId: "acc_primary", poolId: "pool_primary", isActive: true }],
    });

    render(<AccountProxyBinding account={account} admin={admin} busy={false} readOnly onSave={onSave} />);

    expect(screen.getByRole("switch", { name: "Enable account proxy binding" })).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "Account proxy pool" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save binding" })).toBeDisabled();
  });

  it("disables pool testing for read-only guests", () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onTestEndpoint = vi.fn().mockResolvedValue(undefined);
    const account = createAccountSummary({ accountId: "acc_primary" });
    const admin = createUpstreamProxyAdmin({
      bindings: [{ accountId: "acc_primary", poolId: "pool_primary", isActive: true }],
    });

    render(
      <AccountProxyBinding
        account={account}
        admin={admin}
        busy={false}
        readOnly
        onSave={onSave}
        onTestEndpoint={onTestEndpoint}
      />,
    );

    expect(screen.getByRole("button", { name: "Test pool" })).toBeDisabled();
  });
});
