import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/utils";

import { UsageSectionsMultiSelect } from "./usage-sections-multi-select";

describe("UsageSectionsMultiSelect", () => {
  it("renders an explicitly empty selection with no checked sections", async () => {
    const user = userEvent.setup();

    renderWithProviders(<UsageSectionsMultiSelect value="" onChange={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /None/i }));

    expect(screen.getByRole("menuitemcheckbox", { name: "Upstream limits" })).not.toBeChecked();
    expect(screen.getByRole("menuitemcheckbox", { name: "Account pool usage" })).not.toBeChecked();
  });
});
