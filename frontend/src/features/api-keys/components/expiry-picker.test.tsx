import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { addDays, format } from "date-fns";
import { describe, expect, it, vi } from "vitest";

import { ExpiryPicker } from "./expiry-picker";

describe("ExpiryPicker", () => {
  it("shows 'No expiration' when value is null", () => {
    render(<ExpiryPicker value={null} onChange={vi.fn()} />);

    expect(screen.getByText("No expiration")).toBeInTheDocument();
  });

  it("shows formatted date for a custom value", () => {
    const customDate = addDays(new Date(), 15);
    customDate.setHours(23, 59, 59, 0);

    render(<ExpiryPicker value={customDate} onChange={vi.fn()} />);

    expect(screen.getByText(format(customDate, "yyyy-MM-dd"))).toBeInTheDocument();
  });

  it("calls onChange with null when 'No expiration' is clicked", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const date = addDays(new Date(), 15);
    render(<ExpiryPicker value={date} onChange={onChange} />);

    await user.click(screen.getByRole("button"));
    await user.click(await screen.findByText("No expiration"));

    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("calls onChange with a date when preset is clicked", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ExpiryPicker value={null} onChange={onChange} />);

    await user.click(screen.getByRole("button"));
    await user.click(await screen.findByText("30 days"));

    expect(onChange).toHaveBeenCalledOnce();
    const called = onChange.mock.calls[0][0] as Date;
    expect(called).toBeInstanceOf(Date);
    expect(called.getHours()).toBe(23);
    expect(called.getMinutes()).toBe(59);
  });

  it("shows preset options list by default (not calendar)", async () => {
    const user = userEvent.setup();
    render(<ExpiryPicker value={null} onChange={vi.fn()} />);

    await user.click(screen.getByRole("button"));

    expect(await screen.findByText("1 day")).toBeInTheDocument();
    expect(screen.getByText("7 days")).toBeInTheDocument();
    expect(screen.getByText("30 days")).toBeInTheDocument();
    expect(screen.getByText("90 days")).toBeInTheDocument();
    expect(screen.getByText("1 year")).toBeInTheDocument();
    expect(screen.getByText("Custom date...")).toBeInTheDocument();
  });

  it("shows calendar when 'Custom date...' is clicked", async () => {
    const user = userEvent.setup();
    render(<ExpiryPicker value={null} onChange={vi.fn()} />);

    await user.click(screen.getByRole("button"));
    await user.click(await screen.findByText("Custom date..."));

    expect(await screen.findByText(/Back to presets/)).toBeInTheDocument();
  });

  it("goes back to presets from calendar view", async () => {
    const user = userEvent.setup();
    render(<ExpiryPicker value={null} onChange={vi.fn()} />);

    await user.click(screen.getByRole("button"));
    await user.click(await screen.findByText("Custom date..."));
    await user.click(await screen.findByText(/Back to presets/));

    expect(await screen.findByText("1 day")).toBeInTheDocument();
  });

  it("shows multiple No expiration elements when popover open and value is null", async () => {
    const user = userEvent.setup();
    render(<ExpiryPicker value={null} onChange={vi.fn()} />);

    await user.click(screen.getByRole("button"));

    // "No expiration" appears in both the trigger and the popover option
    const allMatches = await screen.findAllByText("No expiration");
    expect(allMatches.length).toBeGreaterThanOrEqual(2);
  });
});
