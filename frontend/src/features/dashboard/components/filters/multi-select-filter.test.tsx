import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MultiSelectFilter, type MultiSelectOption } from "./multi-select-filter";

const OPTIONS: MultiSelectOption[] = [
  { value: "ok", label: "OK" },
  { value: "error", label: "Error" },
  { value: "rate_limit", label: "Rate Limit" },
];

describe("MultiSelectFilter", () => {
  it("renders label when no values selected", () => {
    render(<MultiSelectFilter label="Status" values={[]} options={OPTIONS} onChange={vi.fn()} />);

    expect(screen.getByText("Status")).toBeInTheDocument();
  });

  it("shows option label when one value selected", () => {
    render(<MultiSelectFilter label="Status" values={["ok"]} options={OPTIONS} onChange={vi.fn()} />);

    expect(screen.getByText("OK")).toBeInTheDocument();
  });

  it("shows count when multiple values selected", () => {
    render(
      <MultiSelectFilter label="Status" values={["ok", "error"]} options={OPTIONS} onChange={vi.fn()} />,
    );

    expect(screen.getByText("Status (2)")).toBeInTheDocument();
  });

  it("falls back to count label when option not found for single value", () => {
    render(
      <MultiSelectFilter label="Status" values={["unknown"]} options={OPTIONS} onChange={vi.fn()} />,
    );

    expect(screen.getByText("Status (1)")).toBeInTheDocument();
  });

  it("toggles value on when clicking an unselected option", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<MultiSelectFilter label="Status" values={[]} options={OPTIONS} onChange={onChange} />);

    await user.click(screen.getByRole("button"));
    await user.click(await screen.findByText("OK"));

    expect(onChange).toHaveBeenCalledWith(["ok"]);
  });

  it("toggles value off when clicking a selected option", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <MultiSelectFilter label="Status" values={["ok", "error"]} options={OPTIONS} onChange={onChange} />,
    );

    await user.click(screen.getByRole("button"));
    await user.click(await screen.findByText("OK"));

    expect(onChange).toHaveBeenCalledWith(["error"]);
  });

  it("shows 'No options' when options array is empty", async () => {
    const user = userEvent.setup();
    render(<MultiSelectFilter label="Status" values={[]} options={[]} onChange={vi.fn()} />);

    await user.click(screen.getByRole("button"));

    expect(await screen.findByText("No options")).toBeInTheDocument();
  });
});
