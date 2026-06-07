import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { createAccountSummary } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

import { ApiKeyCreateDialog } from "./api-key-create-dialog";

describe("ApiKeyCreateDialog", () => {
  it("shows the codex /model checkbox unchecked by default", () => {
    renderWithProviders(
      <ApiKeyCreateDialog
        open
        busy={false}
        onOpenChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByRole("checkbox", { name: "Apply to codex /model" })).not.toBeChecked();
  });

  it("submits the codex /model checkbox value", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    renderWithProviders(
      <ApiKeyCreateDialog
        open
        busy={false}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    await user.type(screen.getByLabelText("Name"), "Codex key");
    await user.click(screen.getByRole("checkbox", { name: "Apply to codex /model" }));
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    expect(onSubmit.mock.calls[0][0].applyToCodexModel).toBe(true);
  });

  it("submits opportunistic traffic class", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    renderWithProviders(
      <ApiKeyCreateDialog
        open
        busy={false}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    await user.type(screen.getByLabelText("Name"), "Opportunistic key");
    await user.click(screen.getByRole("combobox", { name: /traffic class/i }));
    await user.click(await screen.findByRole("option", { name: /opportunistic/i }));
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    expect(onSubmit.mock.calls[0][0].trafficClass).toBe("opportunistic");
  });

  it("resets the codex /model checkbox when the dialog is dismissed", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    const { rerender } = renderWithProviders(
      <ApiKeyCreateDialog
        open
        busy={false}
        onOpenChange={onOpenChange}
        onSubmit={onSubmit}
      />,
    );

    const checkbox = screen.getByRole("checkbox", { name: "Apply to codex /model" });
    await user.click(checkbox);
    expect(checkbox).toBeChecked();

    rerender(
      <ApiKeyCreateDialog
        open={false}
        busy={false}
        onOpenChange={onOpenChange}
        onSubmit={onSubmit}
      />,
    );

    rerender(
      <ApiKeyCreateDialog
        open
        busy={false}
        onOpenChange={onOpenChange}
        onSubmit={onSubmit}
      />,
    );

    expect(screen.getByRole("checkbox", { name: "Apply to codex /model" })).not.toBeChecked();
  });

  it("omits assigned accounts when left at all accounts", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    renderWithProviders(
      <ApiKeyCreateDialog
        open
        busy={false}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    await user.type(screen.getByLabelText("Name"), "Scoped create");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    const payload = onSubmit.mock.calls[0][0];
    expect(payload.name).toBe("Scoped create");
    expect("assignedAccountIds" in payload).toBe(false);
  });

  it("submits selected assigned accounts on create", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    server.use(
      http.get("/api/accounts", () =>
        HttpResponse.json({
          accounts: [
            createAccountSummary(),
            createAccountSummary({
              accountId: "acc_secondary",
              email: "secondary@example.com",
              displayName: "secondary@example.com",
            }),
          ],
        }),
      ),
    );

    renderWithProviders(
      <ApiKeyCreateDialog
        open
        busy={false}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    await user.type(screen.getByLabelText("Name"), "Scoped create");
    await user.click(await screen.findByRole("button", { name: "All accounts" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: /primary@example\.com/i }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: /secondary@example\.com/i }));
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    const payload = onSubmit.mock.calls[0][0];
    expect(payload.assignedAccountIds).toEqual(["acc_primary", "acc_secondary"]);
  });

  it("clears selected assigned accounts when the dialog is dismissed", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    function Harness() {
      const [open, setOpen] = useState(true);

      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            Reopen
          </button>
          <ApiKeyCreateDialog
            open={open}
            busy={false}
            onOpenChange={setOpen}
            onSubmit={onSubmit}
          />
        </>
      );
    }

    renderWithProviders(<Harness />);

    await user.click(await screen.findByRole("button", { name: "All accounts" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: /primary@example\.com/i }));
    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "1 account selected" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Close" }));
    await user.click(screen.getByRole("button", { name: "Reopen" }));

    expect(await screen.findByRole("button", { name: "All accounts" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "1 account selected" })).not.toBeInTheDocument();
  });
});
