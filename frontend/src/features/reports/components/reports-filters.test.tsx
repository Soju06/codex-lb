import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReportsFilters, type ReportsFiltersState } from "./reports-filters";
import { REPORT_CHART_DEFINITIONS } from "../hooks/use-report-chart-visibility";

const FILTERS: ReportsFiltersState = {
  startDate: "2026-06-01",
  endDate: "2026-06-07",
  accountId: [],
  apiKeyId: [],
  model: "",
  useragent: "",
};

const ALL_CHART_IDS = REPORT_CHART_DEFINITIONS.map(({ id }) => id);
const ALL_CHART_LABELS = [
  "Cost by Day",
  "Tokens by Day",
  "Time to First Token",
  "Tokens per Second",
  "Queue Wait",
];

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
        apiKeyOptions={[]}
        modelOptions={[]}
        useragentOptions={[]}
        visibleChartIds={ALL_CHART_IDS}
        onVisibleChartIdsChange={vi.fn()}
        onPresetSelect={vi.fn()}
        onFiltersChange={onFiltersChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: /accounts/i }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: /primary account/i }));

    expect(onFiltersChange).toHaveBeenCalledWith({ ...FILTERS, accountId: ["acc_one"] });
  });

  it("updates API key filters from the API key selector", async () => {
    const user = userEvent.setup();
    const onFiltersChange = vi.fn();
    render(
      <ReportsFilters
        filters={FILTERS}
        selectedPresetDays={7}
        accountOptions={[]}
        apiKeyOptions={[{ value: "key_one", label: "Dev Key · key-123" }]}
        modelOptions={[]}
        useragentOptions={[]}
        visibleChartIds={ALL_CHART_IDS}
        onVisibleChartIdsChange={vi.fn()}
        onPresetSelect={vi.fn()}
        onFiltersChange={onFiltersChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: /api keys/i }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: /dev key · key-123/i }));

    expect(onFiltersChange).toHaveBeenCalledWith({ ...FILTERS, apiKeyId: ["key_one"] });
  });

  it("keeps the reports model filter as a single selected value", async () => {
    const user = userEvent.setup();
    const onFiltersChange = vi.fn();
    render(
      <ReportsFilters
        filters={{ ...FILTERS, model: "gpt-5.1" }}
        selectedPresetDays={7}
        accountOptions={[]}
        apiKeyOptions={[]}
        modelOptions={[
          { value: "gpt-5.1", label: "gpt-5.1" },
          { value: "gpt-5.2", label: "gpt-5.2" },
        ]}
        useragentOptions={[]}
        visibleChartIds={ALL_CHART_IDS}
        onVisibleChartIdsChange={vi.fn()}
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
        apiKeyOptions={[]}
        modelOptions={[]}
        useragentOptions={[
          { value: "CLI", label: "CLI" },
          { value: "SDK", label: "SDK" },
        ]}
        visibleChartIds={ALL_CHART_IDS}
        onVisibleChartIdsChange={vi.fn()}
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
        apiKeyOptions={[]}
        modelOptions={[]}
        useragentOptions={[]}
        visibleChartIds={ALL_CHART_IDS}
        onVisibleChartIdsChange={vi.fn()}
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

  it("applies reciprocal bounds while keeping today as the end-date ceiling", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-12T12:00:00"));

    const { container } = render(
      <ReportsFilters
        filters={FILTERS}
        selectedPresetDays={30}
        accountOptions={[]}
        apiKeyOptions={[]}
        modelOptions={[]}
        useragentOptions={[]}
        visibleChartIds={ALL_CHART_IDS}
        onVisibleChartIdsChange={vi.fn()}
        onPresetSelect={vi.fn()}
        onFiltersChange={vi.fn()}
      />,
    );

    const dateInputs = container.querySelectorAll<HTMLInputElement>('input[type="date"]');
    expect(dateInputs).toHaveLength(2);
    expect(dateInputs[0]).toHaveAttribute("max", FILTERS.endDate);
    expect(dateInputs[1]).toHaveAttribute("min", FILTERS.startDate);
    expect(dateInputs[1]).toHaveAttribute("max", "2026-06-12");
  });

  it("keeps today as the start-date ceiling when the end date is later", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-12T12:00:00"));

    const { container } = render(
      <ReportsFilters
        filters={{ ...FILTERS, endDate: "2026-06-13" }}
        selectedPresetDays={null}
        accountOptions={[]}
        apiKeyOptions={[]}
        modelOptions={[]}
        useragentOptions={[]}
        visibleChartIds={ALL_CHART_IDS}
        onVisibleChartIdsChange={vi.fn()}
        onPresetSelect={vi.fn()}
        onFiltersChange={vi.fn()}
      />,
    );

    const dateInputs = container.querySelectorAll<HTMLInputElement>('input[type="date"]');
    expect(dateInputs[0]).toHaveAttribute("max", "2026-06-12");
  });

  it("links both invalid date inputs to one corrective message", () => {
    const { container } = render(
      <ReportsFilters
        filters={{ ...FILTERS, startDate: "2026-06-08" }}
        selectedPresetDays={null}
        accountOptions={[]}
        apiKeyOptions={[]}
        modelOptions={[]}
        useragentOptions={[]}
        visibleChartIds={ALL_CHART_IDS}
        onVisibleChartIdsChange={vi.fn()}
        onPresetSelect={vi.fn()}
        onFiltersChange={vi.fn()}
      />,
    );

    const dateInputs = container.querySelectorAll<HTMLInputElement>('input[type="date"]');
    const message = screen.getByText("Start date must be on or before end date.");
    const descriptionId = message.getAttribute("id");

    expect(descriptionId).toBeTruthy();
    expect(dateInputs[0]).toHaveAttribute("aria-invalid", "true");
    expect(dateInputs[1]).toHaveAttribute("aria-invalid", "true");
    expect(dateInputs[0]).toHaveAttribute("aria-describedby", descriptionId);
    expect(dateInputs[1]).toHaveAttribute("aria-describedby", descriptionId);
    expect(message).toHaveAttribute("aria-live", "polite");
  });

  it("summarizes the default chart selection", () => {
    render(
      <ReportsFilters
        filters={FILTERS}
        selectedPresetDays={7}
        accountOptions={[]}
        apiKeyOptions={[]}
        modelOptions={[]}
        useragentOptions={[]}
        visibleChartIds={ALL_CHART_IDS}
        onVisibleChartIdsChange={vi.fn()}
        onPresetSelect={vi.fn()}
        onFiltersChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Charts (5)" })).toBeInTheDocument();
  });

  it("places the chart selector before the start date", () => {
    const { container } = render(
      <ReportsFilters
        filters={FILTERS}
        selectedPresetDays={7}
        accountOptions={[]}
        apiKeyOptions={[]}
        modelOptions={[]}
        useragentOptions={[]}
        visibleChartIds={ALL_CHART_IDS}
        onVisibleChartIdsChange={vi.fn()}
        onPresetSelect={vi.fn()}
        onFiltersChange={vi.fn()}
      />,
    );

    const chartButton = screen.getByRole("button", { name: "Charts (5)" });
    const startDate = container.querySelector('input[name="report-start-date"]');

    expect(startDate).not.toBeNull();
    expect(chartButton.compareDocumentPosition(startDate!)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it("exposes chart options in canonical order", async () => {
    const user = userEvent.setup();
    render(
      <ReportsFilters
        filters={FILTERS}
        selectedPresetDays={7}
        accountOptions={[]}
        apiKeyOptions={[]}
        modelOptions={[]}
        useragentOptions={[]}
        visibleChartIds={ALL_CHART_IDS}
        onVisibleChartIdsChange={vi.fn()}
        onPresetSelect={vi.fn()}
        onFiltersChange={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Charts (5)" }));

    expect(screen.getAllByRole("menuitemcheckbox").map((item) => item.textContent)).toEqual(
      ALL_CHART_LABELS,
    );
  });

  it("returns the other four chart IDs when Queue Wait is toggled off", async () => {
    const user = userEvent.setup();
    const onVisibleChartIdsChange = vi.fn();
    render(
      <ReportsFilters
        filters={FILTERS}
        selectedPresetDays={7}
        accountOptions={[]}
        apiKeyOptions={[]}
        modelOptions={[]}
        useragentOptions={[]}
        visibleChartIds={ALL_CHART_IDS}
        onVisibleChartIdsChange={onVisibleChartIdsChange}
        onPresetSelect={vi.fn()}
        onFiltersChange={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Charts (5)" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: "Queue Wait" }));

    expect(onVisibleChartIdsChange).toHaveBeenCalledWith(
      ALL_CHART_IDS.filter((id) => id !== "queueWait"),
    );
  });

  it("keeps all chart options available when the selection is empty", async () => {
    const user = userEvent.setup();
    render(
      <ReportsFilters
        filters={FILTERS}
        selectedPresetDays={7}
        accountOptions={[]}
        apiKeyOptions={[]}
        modelOptions={[]}
        useragentOptions={[]}
        visibleChartIds={[]}
        onVisibleChartIdsChange={vi.fn()}
        onPresetSelect={vi.fn()}
        onFiltersChange={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Charts" }));

    expect(screen.getAllByRole("menuitemcheckbox").map((item) => item.textContent)).toEqual(
      ALL_CHART_LABELS,
    );
  });
});
