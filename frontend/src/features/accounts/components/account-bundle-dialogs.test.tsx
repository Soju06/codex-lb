import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { usePrivacyStore } from "@/hooks/use-privacy";
import { renderWithProviders } from "@/test/utils";

import { ExportAccountBundleDialog, ImportAccountBundleDialog } from "./account-bundle-dialogs";

const { exportBundle, preflightBundle, commitBundle } = vi.hoisted(() => ({
  exportBundle: vi.fn(),
  preflightBundle: vi.fn(),
  commitBundle: vi.fn(),
}));

vi.mock("@/features/accounts/api", () => ({
  exportAccountBundle: exportBundle,
  preflightAccountBundle: preflightBundle,
  commitAccountBundle: commitBundle,
}));

const accounts = [
  {
    accountId: "account-1",
    email: "first@example.com",
    displayName: "First account",
    planType: "plus",
    status: "active",
    limitWarmupEnabled: false,
    additionalQuotas: [],
  },
  {
    accountId: "account-2",
    email: "second@example.com",
    displayName: "Second account",
    planType: "team",
    status: "paused",
    limitWarmupEnabled: true,
    additionalQuotas: [],
  },
];

describe("account bundle dialogs", () => {
  beforeEach(() => {
    exportBundle.mockReset();
    preflightBundle.mockReset();
    commitBundle.mockReset();
    exportBundle.mockResolvedValue(new Blob(["opaque"]));
    usePrivacyStore.setState({ blurred: false });
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:test");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  });

  it("selects all accounts by default and requires matching passphrases", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <ExportAccountBundleDialog open accounts={accounts} onOpenChange={vi.fn()} />,
    );

    expect(screen.getByRole("checkbox", { name: /First account/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Second account/ })).toBeChecked();
    const download = screen.getByRole("button", { name: "Download bundle" });
    expect(download).toBeDisabled();

    await user.type(screen.getByLabelText("Passphrase"), "bundle-passphrase");
    await user.type(screen.getByLabelText("Confirm passphrase"), "different");
    expect(screen.getByText("Passphrases do not match.")).toBeInTheDocument();
    expect(download).toBeDisabled();

    await user.clear(screen.getByLabelText("Confirm passphrase"));
    await user.type(screen.getByLabelText("Confirm passphrase"), "bundle-passphrase");
    await user.click(download);

    await waitFor(() => {
      expect(exportBundle).toHaveBeenCalledWith(
        expect.arrayContaining(["account-1", "account-2"]),
        "bundle-passphrase",
      );
    });
  });

  it("selects asynchronously loaded accounts", async () => {
    const { rerender } = renderWithProviders(
      <ExportAccountBundleDialog open accounts={[]} onOpenChange={vi.fn()} />,
    );

    rerender(<ExportAccountBundleDialog open accounts={accounts} onOpenChange={vi.fn()} />);
    expect(screen.getByRole("checkbox", { name: /First account/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Second account/ })).toBeChecked();

  });

  it("prunes removed accounts without undoing deliberate selection changes", async () => {
    const user = userEvent.setup();
    const { rerender } = renderWithProviders(
      <ExportAccountBundleDialog open accounts={accounts} onOpenChange={vi.fn()} />,
    );
    await user.click(screen.getByRole("checkbox", { name: /First account/ }));

    const laterAccounts = [
      accounts[0],
      { ...accounts[1], accountId: "account-3", displayName: "Third account" },
    ];
    rerender(<ExportAccountBundleDialog open accounts={laterAccounts} onOpenChange={vi.fn()} />);

    expect(screen.getByRole("checkbox", { name: /First account/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Third account/ })).not.toBeChecked();
    expect(screen.queryByRole("checkbox", { name: /Second account/ })).not.toBeInTheDocument();
  });

  it("honors privacy mode for email-derived export labels", () => {
    usePrivacyStore.setState({ blurred: true });
    renderWithProviders(
      <ExportAccountBundleDialog
        open
        accounts={[{ ...accounts[0], displayName: accounts[0].email }, accounts[1]]}
        onOpenChange={vi.fn()}
      />,
    );

    expect(screen.getByText("first@example.com")).toHaveClass("privacy-blur");
    expect(screen.getByText("Second account")).not.toHaveClass("privacy-blur");
  });

  it("shows only masked preflight data and requires replace confirmation", async () => {
    const user = userEvent.setup();
    const onCommitted = vi.fn().mockResolvedValue(undefined);
    preflightBundle.mockResolvedValue({
      integrityToken: "digest",
      accountCount: 1,
      newCount: 0,
      matchingCount: 1,
      accounts: [{
        index: 0,
        maskedIdentity: "s***@example.com",
        state: "matching",
        metadata: {
          alias: "Portable alias",
          planType: "team",
          routingPolicy: "preserve",
          limitWarmupEnabled: true,
          securityWorkAuthorized: false,
        },
      }],
    });
    commitBundle.mockResolvedValue({
      summary: { imported: 0, replaced: 1, skipped: 0, failed: 0 },
      results: [{ index: 0, outcome: "replaced", destinationAccountId: "account-2", warning: null }],
      warnings: [],
    });
    renderWithProviders(
      <ImportAccountBundleDialog open onOpenChange={vi.fn()} onCommitted={onCommitted} />,
    );

    expect(screen.getByLabelText("Passphrase")).toHaveAttribute("autocomplete", "new-password");
    const file = new File(["opaque-bundle"], "accounts.clb-account-bundle");
    await user.upload(screen.getByLabelText("Encrypted account bundle"), file);
    await user.type(screen.getByLabelText("Passphrase"), "bundle-passphrase");
    await user.click(screen.getByRole("button", { name: "Review bundle" }));

    expect(await screen.findByText("s***@example.com")).toBeInTheDocument();
    expect(screen.queryByText("second@example.com")).not.toBeInTheDocument();
    expect(screen.queryByText(/token/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "Replace matching accounts" }));
    const importButton = screen.getByRole("button", { name: "Import bundle" });
    expect(importButton).toBeDisabled();
    await user.click(screen.getByText(/I understand that matching destination/));
    await user.click(importButton);

    await waitFor(() => {
      expect(commitBundle).toHaveBeenCalledWith(expect.objectContaining({
        file,
        passphrase: "bundle-passphrase",
        integrityToken: "digest",
        conflictMode: "replace",
        confirmReplace: true,
      }));
      expect(onCommitted).toHaveBeenCalled();
    });
    expect(await screen.findByText("Imported 0, replaced 1, skipped 0, failed 0.")).toBeInTheDocument();
  });

  it("clears retained upload state when returning from preflight", async () => {
    const user = userEvent.setup();
    preflightBundle.mockResolvedValue({
      integrityToken: "digest",
      accountCount: 1,
      newCount: 1,
      matchingCount: 0,
      accounts: [],
    });
    renderWithProviders(
      <ImportAccountBundleDialog open onOpenChange={vi.fn()} onCommitted={vi.fn()} />,
    );

    await user.upload(
      screen.getByLabelText("Encrypted account bundle"),
      new File(["opaque-bundle"], "accounts.clb-account-bundle"),
    );
    await user.type(screen.getByLabelText("Passphrase"), "bundle-passphrase");
    await user.click(screen.getByRole("button", { name: "Review bundle" }));

    await user.click(await screen.findByRole("button", { name: "Back" }));

    expect(screen.getByLabelText("Passphrase")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Review bundle" })).toBeDisabled();
    expect(preflightBundle).toHaveBeenCalledOnce();
  });

  it("requires replacement confirmation again after returning to choose another bundle", async () => {
    const user = userEvent.setup();
    preflightBundle
      .mockResolvedValueOnce({
        integrityToken: "digest-a",
        accountCount: 1,
        newCount: 0,
        matchingCount: 1,
        accounts: [],
      })
      .mockResolvedValueOnce({
        integrityToken: "digest-b",
        accountCount: 1,
        newCount: 0,
        matchingCount: 1,
        accounts: [],
      });
    renderWithProviders(
      <ImportAccountBundleDialog open onOpenChange={vi.fn()} onCommitted={vi.fn()} />,
    );

    await user.upload(
      screen.getByLabelText("Encrypted account bundle"),
      new File(["bundle-a"], "a.clb-account-bundle"),
    );
    await user.type(screen.getByLabelText("Passphrase"), "bundle-passphrase");
    await user.click(screen.getByRole("button", { name: "Review bundle" }));
    await user.click(await screen.findByRole("radio", { name: "Replace matching accounts" }));
    await user.click(screen.getByText(/I understand that matching destination/));

    await user.click(screen.getByRole("button", { name: "Back" }));
    await user.upload(
      screen.getByLabelText("Encrypted account bundle"),
      new File(["bundle-b"], "b.clb-account-bundle"),
    );
    await user.type(screen.getByLabelText("Passphrase"), "bundle-passphrase");
    await user.click(screen.getByRole("button", { name: "Review bundle" }));

    expect(await screen.findByRole("radio", { name: "Skip matching accounts" })).toBeChecked();
    expect(screen.queryByText(/I understand that matching destination/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "Replace matching accounts" }));
    expect(screen.getByRole("button", { name: "Import bundle" })).toBeDisabled();
  });

  it("shows a successful commit even when the account refresh fails", async () => {
    const user = userEvent.setup();
    const onCommitted = vi.fn().mockRejectedValue(new Error("refresh failed"));
    preflightBundle.mockResolvedValue({
      integrityToken: "digest",
      accountCount: 1,
      newCount: 1,
      matchingCount: 0,
      accounts: [],
    });
    commitBundle.mockResolvedValue({
      summary: { imported: 1, replaced: 0, skipped: 0, failed: 0 },
      results: [{ index: 0, outcome: "imported", destinationAccountId: "account-1", warning: null }],
      warnings: [],
    });
    renderWithProviders(
      <ImportAccountBundleDialog open onOpenChange={vi.fn()} onCommitted={onCommitted} />,
    );

    await user.upload(
      screen.getByLabelText("Encrypted account bundle"),
      new File(["opaque-bundle"], "accounts.clb-account-bundle"),
    );
    await user.type(screen.getByLabelText("Passphrase"), "bundle-passphrase");
    await user.click(screen.getByRole("button", { name: "Review bundle" }));
    await user.click(await screen.findByRole("button", { name: "Import bundle" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Imported 1, replaced 0, skipped 0, failed 0.",
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Import bundle" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Passphrase")).not.toBeInTheDocument();
    expect(commitBundle).toHaveBeenCalledOnce();
    expect(onCommitted).toHaveBeenCalledOnce();
  });

  it("ignores an export completion after close and reopen", async () => {
    const user = userEvent.setup();
    let resolveExport!: (blob: Blob) => void;
    exportBundle.mockReturnValue(new Promise((resolve) => { resolveExport = resolve; }));
    vi.mocked(URL.createObjectURL).mockClear();
    const { rerender } = renderWithProviders(
      <ExportAccountBundleDialog open accounts={accounts} onOpenChange={vi.fn()} />,
    );
    await user.type(screen.getByLabelText("Passphrase"), "bundle-passphrase");
    await user.type(screen.getByLabelText("Confirm passphrase"), "bundle-passphrase");
    await user.click(screen.getByRole("button", { name: "Download bundle" }));

    rerender(<ExportAccountBundleDialog open={false} accounts={accounts} onOpenChange={vi.fn()} />);
    rerender(<ExportAccountBundleDialog open accounts={accounts} onOpenChange={vi.fn()} />);
    resolveExport(new Blob(["opaque"]));

    await waitFor(() => expect(URL.createObjectURL).not.toHaveBeenCalled());
    expect(screen.getByLabelText("Passphrase")).toHaveValue("");
  });

  it("ignores a preflight completion after close and reopen", async () => {
    const user = userEvent.setup();
    let resolvePreflight!: (preview: Awaited<ReturnType<typeof preflightBundle>>) => void;
    preflightBundle.mockReturnValue(new Promise((resolve) => { resolvePreflight = resolve; }));
    const { rerender } = renderWithProviders(
      <ImportAccountBundleDialog open onOpenChange={vi.fn()} onCommitted={vi.fn()} />,
    );
    await user.upload(
      screen.getByLabelText("Encrypted account bundle"),
      new File(["opaque-bundle"], "accounts.clb-account-bundle"),
    );
    await user.type(screen.getByLabelText("Passphrase"), "bundle-passphrase");
    await user.click(screen.getByRole("button", { name: "Review bundle" }));

    rerender(<ImportAccountBundleDialog open={false} onOpenChange={vi.fn()} onCommitted={vi.fn()} />);
    rerender(<ImportAccountBundleDialog open onOpenChange={vi.fn()} onCommitted={vi.fn()} />);
    resolvePreflight({
      integrityToken: "digest",
      accountCount: 1,
      newCount: 1,
      matchingCount: 0,
      accounts: [],
    });

    await waitFor(() => expect(screen.getByLabelText("Passphrase")).toHaveValue(""));
    expect(screen.queryByRole("button", { name: "Import bundle" })).not.toBeInTheDocument();
  });

  it("invalidates account queries after a commit completes following close and reopen", async () => {
    const user = userEvent.setup();
    const onCommitted = vi.fn().mockResolvedValue(undefined);
    preflightBundle.mockResolvedValue({
      integrityToken: "digest",
      accountCount: 1,
      newCount: 1,
      matchingCount: 0,
      accounts: [],
    });
    let resolveCommit!: (result: Awaited<ReturnType<typeof commitBundle>>) => void;
    commitBundle.mockReturnValue(new Promise((resolve) => { resolveCommit = resolve; }));
    const { rerender } = renderWithProviders(
      <ImportAccountBundleDialog open onOpenChange={vi.fn()} onCommitted={onCommitted} />,
    );
    await user.upload(
      screen.getByLabelText("Encrypted account bundle"),
      new File(["opaque-bundle"], "accounts.clb-account-bundle"),
    );
    await user.type(screen.getByLabelText("Passphrase"), "bundle-passphrase");
    await user.click(screen.getByRole("button", { name: "Review bundle" }));
    await user.click(await screen.findByRole("button", { name: "Import bundle" }));

    rerender(
      <ImportAccountBundleDialog open={false} onOpenChange={vi.fn()} onCommitted={onCommitted} />,
    );
    rerender(<ImportAccountBundleDialog open onOpenChange={vi.fn()} onCommitted={onCommitted} />);
    resolveCommit({
      summary: { imported: 1, replaced: 0, skipped: 0, failed: 0 },
      results: [{ index: 0, outcome: "imported", destinationAccountId: "account-1", warning: null }],
      warnings: [],
    });

    await waitFor(() => expect(onCommitted).toHaveBeenCalledOnce());
    expect(screen.getByLabelText("Passphrase")).toHaveValue("");
    expect(screen.queryByText("Imported 1, replaced 0, skipped 0, failed 0.")).not.toBeInTheDocument();
  });
});
