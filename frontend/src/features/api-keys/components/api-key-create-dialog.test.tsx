import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/utils";

import { ApiKeyCreateDialog } from "./api-key-create-dialog";

describe("ApiKeyCreateDialog", () => {
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
