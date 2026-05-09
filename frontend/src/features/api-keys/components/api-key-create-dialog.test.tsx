import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/utils";

import { ApiKeyCreateDialog } from "./api-key-create-dialog";

describe("ApiKeyCreateDialog", () => {
  it("submits traffic class", async () => {
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

    expect(
      screen.getByRole("combobox", { name: "Traffic class" }),
    ).toHaveTextContent("Foreground");

    await user.type(screen.getByLabelText("Name"), "Foreground key");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    expect(onSubmit.mock.calls[0][0]).toMatchObject({
      name: "Foreground key",
      trafficClass: "foreground",
    });
  });
});
