import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PaginationControls } from "./pagination-controls";

describe("PaginationControls", () => {
  it("disables First and Previous buttons on first page", () => {
    render(
      <PaginationControls
        total={42}
        limit={10}
        offset={0}
        hasMore={true}
        onLimitChange={vi.fn()}
        onOffsetChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "First page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next page" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Last page" })).toBeEnabled();
  });

  it("disables Next and Last buttons on last page", () => {
    render(
      <PaginationControls
        total={42}
        limit={10}
        offset={40}
        hasMore={false}
        onLimitChange={vi.fn()}
        onOffsetChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "First page" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Last page" })).toBeDisabled();
  });

  it("shows correct range for first page", () => {
    const { container } = render(
      <PaginationControls
        total={42}
        limit={10}
        offset={0}
        hasMore={true}
        onLimitChange={vi.fn()}
        onOffsetChange={vi.fn()}
      />,
    );

    // Range display: "1–10 of 42"
    const rangeText = container.textContent;
    expect(rangeText).toContain("1");
    expect(rangeText).toContain("10");
    expect(rangeText).toContain("42");
  });

  it("shows zero range for empty results", () => {
    const { container } = render(
      <PaginationControls
        total={0}
        limit={10}
        offset={0}
        hasMore={false}
        onLimitChange={vi.fn()}
        onOffsetChange={vi.fn()}
      />,
    );

    // rangeStart=0, rangeEnd=0, total=0
    const spans = container.querySelectorAll("span.tabular-nums");
    expect(spans.length).toBeGreaterThan(0);
    expect(spans[0].textContent).toContain("0");
  });

  it("navigates to next page", async () => {
    const user = userEvent.setup();
    const onOffsetChange = vi.fn();
    render(
      <PaginationControls
        total={42}
        limit={10}
        offset={0}
        hasMore={true}
        onLimitChange={vi.fn()}
        onOffsetChange={onOffsetChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(onOffsetChange).toHaveBeenCalledWith(10);
  });

  it("navigates to previous page", async () => {
    const user = userEvent.setup();
    const onOffsetChange = vi.fn();
    render(
      <PaginationControls
        total={42}
        limit={10}
        offset={20}
        hasMore={true}
        onLimitChange={vi.fn()}
        onOffsetChange={onOffsetChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Previous page" }));
    expect(onOffsetChange).toHaveBeenCalledWith(10);
  });

  it("navigates to first page", async () => {
    const user = userEvent.setup();
    const onOffsetChange = vi.fn();
    render(
      <PaginationControls
        total={42}
        limit={10}
        offset={20}
        hasMore={true}
        onLimitChange={vi.fn()}
        onOffsetChange={onOffsetChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: "First page" }));
    expect(onOffsetChange).toHaveBeenCalledWith(0);
  });

  it("navigates to last page", async () => {
    const user = userEvent.setup();
    const onOffsetChange = vi.fn();
    render(
      <PaginationControls
        total={42}
        limit={10}
        offset={0}
        hasMore={true}
        onLimitChange={vi.fn()}
        onOffsetChange={onOffsetChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Last page" }));
    // lastPage = (Math.ceil(42/10) - 1) * 10 = 40
    expect(onOffsetChange).toHaveBeenCalledWith(40);
  });

  it("enables all nav buttons on middle page", () => {
    render(
      <PaginationControls
        total={42}
        limit={10}
        offset={20}
        hasMore={true}
        onLimitChange={vi.fn()}
        onOffsetChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "First page" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Next page" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Last page" })).toBeEnabled();
  });
});
