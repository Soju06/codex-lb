import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReportsFilters, type ReportsFiltersState } from "./reports-filters";

const FILTERS: ReportsFiltersState = {
  startDate: "2026-06-01",
  endDate: "2026-06-07",
  accountId: [],
  model: "",
};

describe("ReportsFilters", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("keeps preset ranges inclusive of the selected day count", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 5, 7, 12, 0, 0));
    const onFiltersChange = vi.fn();
    render(
      <ReportsFilters
        filters={FILTERS}
        accountOptions={[]}
        modelOptions={[]}
        onFiltersChange={onFiltersChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "7d" }));

    expect(onFiltersChange).toHaveBeenCalledWith({
      ...FILTERS,
      startDate: "2026-06-01",
      endDate: "2026-06-07",
    });
  });

  it("updates account filters from the account selector", async () => {
    const user = userEvent.setup();
    const onFiltersChange = vi.fn();
    render(
      <ReportsFilters
        filters={FILTERS}
        accountOptions={[{ value: "acc_one", label: "Primary account", isEmail: false }]}
        modelOptions={[]}
        onFiltersChange={onFiltersChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: /accounts/i }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: /primary account/i }));

    expect(onFiltersChange).toHaveBeenCalledWith({ ...FILTERS, accountId: ["acc_one"] });
  });

  it("keeps the reports model filter as a single selected value", async () => {
    const user = userEvent.setup();
    const onFiltersChange = vi.fn();
    render(
      <ReportsFilters
        filters={{ ...FILTERS, model: "gpt-5.1" }}
        accountOptions={[]}
        modelOptions={[
          { value: "gpt-5.1", label: "gpt-5.1" },
          { value: "gpt-5.2", label: "gpt-5.2" },
        ]}
        onFiltersChange={onFiltersChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: /gpt-5.1/i }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: /gpt-5.2/i }));

    expect(onFiltersChange).toHaveBeenCalledWith({
      ...FILTERS,
      model: "gpt-5.2",
    });
  });
});
