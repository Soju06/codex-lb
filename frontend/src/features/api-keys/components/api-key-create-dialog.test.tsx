import { useState, type ComponentProps } from "react";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/utils";

import { ApiKeyCreateDialog } from "./api-key-create-dialog";

type ControlledCreateDialogProps = {
  busy: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: ComponentProps<typeof ApiKeyCreateDialog>["onSubmit"];
};

function ControlledCreateDialog({ busy, onOpenChange, onSubmit }: ControlledCreateDialogProps) {
  const [open, setOpen] = useState(true);

  return (
    <ApiKeyCreateDialog
      open={open}
      busy={busy}
      onOpenChange={(nextOpen) => {
        onOpenChange(nextOpen);
        setOpen(nextOpen);
      }}
      onSubmit={onSubmit}
    />
  );
}

function ReopenableCreateDialog({ onSubmit }: { onSubmit: ComponentProps<typeof ApiKeyCreateDialog>["onSubmit"] }) {
  const [open, setOpen] = useState(true);

  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open create dialog
      </button>
      <ApiKeyCreateDialog open={open} busy={false} onOpenChange={setOpen} onSubmit={onSubmit} />
    </>
  );
}

describe("ApiKeyCreateDialog", () => {
  it("submits peer fallback base URLs", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const onOpenChange = vi.fn();

    renderWithProviders(
      <ControlledCreateDialog
        busy={false}
        onOpenChange={onOpenChange}
        onSubmit={onSubmit}
      />,
    );

    const dialog = screen.getByRole("dialog", { name: "Create API key" });
    await user.type(within(dialog).getByLabelText("Name"), "Peer fallback key");
    await user.type(within(dialog).getByLabelText("Peer fallback base URL"), "http://127.0.0.1:2461");
    await user.click(within(dialog).getByRole("button", { name: "Add URL" }));
    await user.click(within(dialog).getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false);
    });

    const payload = onSubmit.mock.calls[0][0];
    expect(payload.name).toBe("Peer fallback key");
    expect(payload.peerFallbackBaseUrls).toEqual(["http://127.0.0.1:2461"]);
  });

  it("clears peer fallback URLs after the dialog is canceled", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    renderWithProviders(<ReopenableCreateDialog onSubmit={onSubmit} />);

    let dialog = screen.getByRole("dialog", { name: "Create API key" });
    await user.type(within(dialog).getByLabelText("Name"), "Canceled key");
    await user.type(within(dialog).getByLabelText("Peer fallback base URL"), "http://127.0.0.1:2461");
    await user.click(within(dialog).getByRole("button", { name: "Add URL" }));
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("button", { name: "Open create dialog" }));
    dialog = screen.getByRole("dialog", { name: "Create API key" });
    await user.type(within(dialog).getByLabelText("Name"), "Fresh key");
    await user.click(within(dialog).getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    const payload = onSubmit.mock.calls[0][0];
    expect(payload.name).toBe("Fresh key");
    expect(payload.peerFallbackBaseUrls).toBeUndefined();
  });
});
