import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReportsFilters, type ReportsFiltersState } from "./reports-filters";

const FILTERS: ReportsFiltersState = {
  startDate: "2026-06-01",
  endDate: "2026-06-07",
  accountId: [],
  model: "",
  useragent: "",
};

describe("ReportsFilters", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("updates account filters from the account selector", async () => {
    const user = userEvent.setup();
    const onFiltersChange = vi.fn();
    render(
      <ReportsFilters
        filters={FILTERS}
        selectedPresetDays={7}
        accountOptions={[{ value: "acc_one", label: "Primary account", isEmail: false }]}
        modelOptions={[]}
        useragentOptions={[]}
        onPresetSelect={vi.fn()}
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
        selectedPresetDays={7}
        accountOptions={[]}
        modelOptions={[
          { value: "gpt-5.1", label: "gpt-5.1" },
          { value: "gpt-5.2", label: "gpt-5.2" },
        ]}
        useragentOptions={[]}
        onPresetSelect={vi.fn()}
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

  it("keeps the reports user-agent filter as a single selected value", async () => {
    const user = userEvent.setup();
    const onFiltersChange = vi.fn();
    render(
      <ReportsFilters
        filters={{ ...FILTERS, useragent: "CLI" }}
        selectedPresetDays={7}
        accountOptions={[]}
        modelOptions={[]}
        useragentOptions={[
          { value: "CLI", label: "CLI" },
          { value: "SDK", label: "SDK" },
        ]}
        onPresetSelect={vi.fn()}
        onFiltersChange={onFiltersChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: /^CLI$/i }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: /^SDK$/i }));

    expect(onFiltersChange).toHaveBeenCalledWith({
      ...FILTERS,
      useragent: "SDK",
    });
  });

  it("renders the selected preset as pressed and forwards preset clicks", () => {
    const onFiltersChange = vi.fn();
    const onPresetSelect = vi.fn();

    render(
      <ReportsFilters
        filters={FILTERS}
        selectedPresetDays={30}
        accountOptions={[]}
        modelOptions={[]}
        useragentOptions={[]}
        onPresetSelect={onPresetSelect}
        onFiltersChange={onFiltersChange}
      />,
    );

    const button7d = screen.getByRole("button", { name: "7d" });
    const button30d = screen.getByRole("button", { name: "30d" });

    expect(button7d).toHaveAttribute("aria-pressed", "false");
    expect(button7d).toHaveAttribute("data-variant", "outline");
    expect(button30d).toHaveAttribute("aria-pressed", "true");
    expect(button30d).toHaveAttribute("data-variant", "default");

    fireEvent.click(screen.getByRole("button", { name: "90d" }));

    expect(onPresetSelect).toHaveBeenCalledWith(90);
  });

  it("limits both date inputs to the current browser-local day", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-12T12:00:00"));

    const { container } = render(
      <ReportsFilters
        filters={FILTERS}
        selectedPresetDays={30}
        accountOptions={[]}
        modelOptions={[]}
        useragentOptions={[]}
        onPresetSelect={vi.fn()}
        onFiltersChange={vi.fn()}
      />,
    );

    const dateInputs = container.querySelectorAll<HTMLInputElement>('input[type="date"]');
    expect(dateInputs).toHaveLength(2);
    expect(dateInputs[0]).toHaveAttribute("max", "2026-06-12");
    expect(dateInputs[1]).toHaveAttribute("max", "2026-06-12");
  });
});
