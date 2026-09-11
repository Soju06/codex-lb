import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RequestFilters, type RequestFiltersProps } from "@/features/dashboard/components/filters/request-filters";
import type { FilterState } from "@/features/dashboard/schemas";

const EMPTY_OPTIONS: RequestFiltersProps["accountOptions"] = [];
const BASE_FILTERS: FilterState = {
  search: "",
  timeframe: "all",
  accountIds: [],
  apiKeyIds: [],
  modelOptions: [],
  statuses: [],
  sources: [],
  conversationId: null,
  limit: 25,
  offset: 0,
};

function renderFilters(
  overrides: Partial<FilterState> = {},
  statusOptions: RequestFiltersProps["statusOptions"] = EMPTY_OPTIONS,
  extraProps: Partial<RequestFiltersProps> = {},
) {
  const filters = { ...BASE_FILTERS, ...overrides };
  const props: RequestFiltersProps = {
    filters,
    accountOptions: EMPTY_OPTIONS,
    apiKeyOptions: EMPTY_OPTIONS,
    modelOptions: EMPTY_OPTIONS,
    statusOptions,
    ...extraProps,
    onSearchChange: vi.fn(),
    onTimeframeChange: vi.fn(),
    onAccountChange: vi.fn(),
    onApiKeyChange: vi.fn(),
    onModelChange: vi.fn(),
    onStatusChange: vi.fn(),
    onConversationDismiss: vi.fn(),
    onReset: vi.fn(),
  };
  render(<RequestFilters {...props} />);
  return props;
}

describe("RequestFilters API key filter", () => {
  it("renders the API key filter by default", () => {
    renderFilters();

    expect(screen.getByRole("button", { name: "API Keys" })).toBeInTheDocument();
  });

  it("hides the API key filter when showApiKeyFilter is false", () => {
    renderFilters({}, EMPTY_OPTIONS, { showApiKeyFilter: false });

    expect(screen.queryByRole("button", { name: "API Keys" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accounts" })).toBeInTheDocument();
  });
});

describe("RequestFilters conversation badge", () => {
  it("renders cancelled as a selectable status option", async () => {
    const user = userEvent.setup();
    const props = renderFilters({}, [{ value: "cancelled", label: "Cancelled" }]);

    await user.click(screen.getByRole("button", { name: "Statuses" }));
    const [option] = await screen.findAllByRole("menuitemcheckbox");
    expect(option).toBeDefined();
    if (!option) {
      throw new Error("Expected the cancelled status option");
    }

    await user.click(option);

    expect(props.onStatusChange).toHaveBeenCalledWith(["cancelled"]);
  });

  it("renders no badge when conversationId is null", () => {
    renderFilters();
    expect(screen.queryByText(/conv/i)).not.toBeInTheDocument();
  });

  it("renders badge when conversationId is set", () => {
    renderFilters({ conversationId: "conv_badge_test" });
    expect(screen.getByText(/conv_badge_test/)).toBeInTheDocument();
  });

  it("places badge immediately after Statuses and before Reset button", () => {
    renderFilters({
      conversationId: "conv_ordering",
      statuses: ["ok"],
    });

    const badgeText = screen.getByText(/conv_ordering/);
    const badgeEl = badgeText.closest('[data-slot="badge"]');
    expect(badgeEl).not.toBeNull();

    const resetButton = screen.getByRole("button", { name: /reset/i });
    const filterRow = resetButton.closest(".flex.flex-wrap");
    expect(filterRow).not.toBeNull();

    const children = Array.from(filterRow?.children ?? []);
    const badgeIdx = children.findIndex((c) => c === badgeEl);
    const resetIdx = children.findIndex((c) => c === resetButton);

    expect(badgeIdx).not.toBe(-1);
    expect(badgeIdx).toBeLessThan(resetIdx);
    expect(badgeIdx).toBeGreaterThan(0);

    // Find the last MultiSelectFilter trigger before the badge
    const filterTriggers = filterRow?.querySelectorAll("button[aria-haspopup=\"menu\"]");
    const lastTrigger = filterTriggers?.[filterTriggers.length - 1];
    expect(lastTrigger).toBeDefined();

    const statusIdx = children.findIndex((c) => c === lastTrigger);
    expect(statusIdx).toBeGreaterThan(-1);
    expect(statusIdx + 1).toBe(badgeIdx);
    expect(badgeIdx + 1).toBe(resetIdx);
  });

  it("dismiss button fires onConversationDismiss", () => {
    const props = renderFilters({ conversationId: "conv_dismiss_me" });

    const dismissButton = screen.getByRole("button", { name: /remove conversation/i });
    fireEvent.click(dismissButton);

    expect(props.onConversationDismiss).toHaveBeenCalled();
  });

  it("dismiss button does not fire onReset", () => {
    const props = renderFilters({ conversationId: "conv_dismiss_only" });

    const dismissButton = screen.getByRole("button", { name: /remove conversation/i });
    fireEvent.click(dismissButton);

    expect(props.onReset).not.toHaveBeenCalled();
  });

  it("Reset button fires onReset and includes conversation clearing", () => {
    const props = renderFilters({ conversationId: "conv_reset_me" });

    const resetButton = screen.getByRole("button", { name: /reset/i });
    fireEvent.click(resetButton);

    expect(props.onReset).toHaveBeenCalled();
  });

  it("shows full conversation ID as title for accessibility when visually truncated", () => {
    const longId = "conv_this_is_a_very_long_id_that_exceeds_display_width";
    renderFilters({ conversationId: longId });

    const badge = screen.getByText(/conv_this/);
    const span = badge.closest("span");
    expect(span).toHaveAttribute("title", longId);
    expect(span).toHaveClass("truncate");
  });
});

describe("RequestFilters source filter", () => {
  const SOURCE_OPTIONS = [
    { value: "subscription_overflow", label: "Overflow" },
    { value: "subscription_overflow_pinned", label: "Overflow (pinned)" },
  ];

  function renderWithSources(
    sourceOptions: RequestFiltersProps["sourceOptions"],
    overrides: Partial<FilterState> = {},
  ) {
    const filters = { ...BASE_FILTERS, ...overrides };
    const props: RequestFiltersProps = {
      filters,
      accountOptions: EMPTY_OPTIONS,
      apiKeyOptions: EMPTY_OPTIONS,
      modelOptions: EMPTY_OPTIONS,
      statusOptions: EMPTY_OPTIONS,
      sourceOptions,
      onSearchChange: vi.fn(),
      onTimeframeChange: vi.fn(),
      onAccountChange: vi.fn(),
      onApiKeyChange: vi.fn(),
      onModelChange: vi.fn(),
      onStatusChange: vi.fn(),
      onSourceChange: vi.fn(),
      onConversationDismiss: vi.fn(),
      onReset: vi.fn(),
    };
    render(<RequestFilters {...props} />);
    return props;
  }

  it("renders no source control on a default install", () => {
    renderWithSources([]);

    expect(screen.queryByRole("button", { name: "Source" })).not.toBeInTheDocument();
  });

  it("renders no source control when the prop is omitted entirely", () => {
    renderFilters();

    expect(screen.queryByRole("button", { name: "Source" })).not.toBeInTheDocument();
  });

  it("offers both overflow values once overflow has been observed", async () => {
    const user = userEvent.setup();
    const props = renderWithSources(SOURCE_OPTIONS);

    await user.click(screen.getByRole("button", { name: "Source" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: "Overflow" }));

    expect(screen.getByRole("menuitemcheckbox", { name: "Overflow (pinned)" })).toBeInTheDocument();
    expect(props.onSourceChange).toHaveBeenCalledWith(["subscription_overflow"]);
  });

  it("deselects a chosen value on a second click", async () => {
    const user = userEvent.setup();
    const props = renderWithSources(SOURCE_OPTIONS, { sources: ["subscription_overflow"] });

    await user.click(screen.getByRole("button", { name: "Overflow" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: "Overflow" }));

    expect(props.onSourceChange).toHaveBeenCalledWith([]);
  });

  it("keeps a deep-linked source visible and clearable even with no options", async () => {
    const user = userEvent.setup();
    const props = renderWithSources([], { sources: ["subscription_overflow"] });

    // Rendered as a `Stale` chip: the tile is hidden on this install, but the URL
    // carries a filter, so it must not become invisible and un-clearable.
    await user.click(screen.getByRole("button", { name: "subscription_overflow" }));
    await user.click(screen.getByRole("button", { name: "Remove stale subscription_overflow" }));

    expect(props.onSourceChange).toHaveBeenCalledWith([]);
  });
});
