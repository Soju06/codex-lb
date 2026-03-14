import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/utils";

import { AccountTagsCard } from "./account-tags-card";

vi.mock("@/features/accounts/hooks/use-accounts", () => ({
  useAccountTags: () => ({
    data: ["alpha", "beta"],
    isLoading: false,
  }),
}));

describe("AccountTagsCard", () => {
  it("autosaves when tags are selected and deselected", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    renderWithProviders(
      <AccountTagsCard accountId="acc_primary" tags={[]} disabled={false} onSave={onSave} />,
    );

    const trigger = screen.getByRole("button", { name: "No tags" });

    await user.click(trigger);
    await user.click(screen.getByRole("menuitemcheckbox", { name: "alpha" }));

    await waitFor(() => {
      expect(onSave).toHaveBeenNthCalledWith(1, "acc_primary", ["alpha"]);
    });

    await waitFor(() => {
      expect(trigger).toBeEnabled();
      expect(trigger).toHaveTextContent("1 tag selected");
    });

    await user.click(screen.getByRole("menuitemcheckbox", { name: "alpha" }));

    await waitFor(() => {
      expect(onSave).toHaveBeenNthCalledWith(2, "acc_primary", []);
    });
  });

  it("autosaves when a new tag is added", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    renderWithProviders(
      <AccountTagsCard accountId="acc_primary" tags={[]} disabled={false} onSave={onSave} />,
    );

    await user.click(screen.getByRole("button", { name: "No tags" }));
    await user.type(screen.getByPlaceholderText("Search or create tags..."), "urgent");
    await user.click(screen.getByRole("button", { name: 'Add tag "urgent"' }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith("acc_primary", ["urgent"]);
    });
  });

  it("restores the persisted tags when autosave fails", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockRejectedValue(new Error("save failed"));

    renderWithProviders(
      <AccountTagsCard accountId="acc_primary" tags={["alpha"]} disabled={false} onSave={onSave} />,
    );

    const trigger = screen.getByRole("button", { name: "1 tag selected" });

    await user.click(trigger);
    await user.click(screen.getByRole("menuitemcheckbox", { name: "beta" }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith("acc_primary", ["alpha", "beta"]);
    });

    await waitFor(() => {
      expect(trigger).toBeEnabled();
      expect(trigger).toHaveTextContent("1 tag selected");
    });
  });
});
