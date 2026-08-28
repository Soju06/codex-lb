import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/utils";

import { ReasoningEffortsMultiSelect } from "./reasoning-efforts-multi-select";

describe("ReasoningEffortsMultiSelect", () => {
  it("returns selected efforts in canonical order", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    renderWithProviders(
      <ReasoningEffortsMultiSelect value={["xhigh"]} onChange={onChange} />,
    );

    await user.click(
      screen.getByRole("button", {
        name: "Allowed efforts: 1 effort selected",
      }),
    );
    await user.click(screen.getByRole("menuitemcheckbox", { name: "Low" }));

    expect(onChange).toHaveBeenCalledWith(["low", "xhigh"]);
  });
});
