import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

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
});
