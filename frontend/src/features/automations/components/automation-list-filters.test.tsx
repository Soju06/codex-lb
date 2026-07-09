import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  AutomationJobsFilters,
  AutomationRunsFilters,
} from "@/features/automations/components/automation-list-filters";

const options = [{ value: "a", label: "A" }];

describe("automation-list-filters", () => {
  it("renders jobs filters and emits search/reset events", async () => {
    const user = userEvent.setup();
    const onSearchChange = vi.fn();
    const onReset = vi.fn();

    render(
      <JobsFiltersHarness onSearchChange={onSearchChange} onReset={onReset} />,
    );

    expect(screen.getByRole("textbox", { name: "Search automation jobs" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accounts" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Models" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Statuses" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Type" })).toBeInTheDocument();

    await user.type(screen.getByRole("textbox", { name: "Search automation jobs" }), "ping");
    expect(onSearchChange).toHaveBeenLastCalledWith("ping");
    expect(screen.getByRole("textbox", { name: "Search automation jobs" })).toHaveValue("ping");

    await user.click(screen.getByRole("button", { name: "Reset" }));
    expect(onReset).toHaveBeenCalledTimes(1);
  });

  it("renders runs filters and emits search/reset events", async () => {
    const user = userEvent.setup();
    const onSearchChange = vi.fn();
    const onReset = vi.fn();

    render(
      <RunsFiltersHarness onSearchChange={onSearchChange} onReset={onReset} />,
    );

    expect(screen.getByRole("textbox", { name: "Search automation runs" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Triggers" })).toBeInTheDocument();

    await user.type(screen.getByRole("textbox", { name: "Search automation runs" }), "run-id");
    expect(onSearchChange).toHaveBeenLastCalledWith("run-id");
    expect(screen.getByRole("textbox", { name: "Search automation runs" })).toHaveValue("run-id");

    await user.click(screen.getByRole("button", { name: "Reset" }));
    expect(onReset).toHaveBeenCalledTimes(1);
  });
});

function JobsFiltersHarness(props: {
  onSearchChange: (value: string) => void;
  onReset: () => void;
}) {
  const [filters, setFilters] = useState({
    search: "",
    accountIds: [] as string[],
    models: [] as string[],
    statuses: [] as string[],
    scheduleTypes: [] as string[],
    limit: 25,
    offset: 0,
  });
  return (
    <AutomationJobsFilters
      filters={filters}
      accountOptions={options}
      modelOptions={options}
      statusOptions={options}
      scheduleTypeOptions={options}
      onSearchChange={(value) => {
        setFilters((current) => ({ ...current, search: value }));
        props.onSearchChange(value);
      }}
      onAccountChange={(values) =>
        setFilters((current) => ({ ...current, accountIds: values }))
      }
      onModelChange={(values) =>
        setFilters((current) => ({ ...current, models: values }))
      }
      onStatusChange={(values) =>
        setFilters((current) => ({ ...current, statuses: values }))
      }
      onScheduleTypeChange={(values) =>
        setFilters((current) => ({ ...current, scheduleTypes: values }))
      }
      onReset={props.onReset}
    />
  );
}

function RunsFiltersHarness(props: {
  onSearchChange: (value: string) => void;
  onReset: () => void;
}) {
  const [filters, setFilters] = useState({
    search: "",
    accountIds: [] as string[],
    models: [] as string[],
    statuses: [] as string[],
    triggers: [] as string[],
    limit: 25,
    offset: 0,
  });
  return (
    <AutomationRunsFilters
      filters={filters}
      accountOptions={options}
      modelOptions={options}
      statusOptions={options}
      triggerOptions={options}
      onSearchChange={(value) => {
        setFilters((current) => ({ ...current, search: value }));
        props.onSearchChange(value);
      }}
      onAccountChange={(values) =>
        setFilters((current) => ({ ...current, accountIds: values }))
      }
      onModelChange={(values) =>
        setFilters((current) => ({ ...current, models: values }))
      }
      onStatusChange={(values) =>
        setFilters((current) => ({ ...current, statuses: values }))
      }
      onTriggerChange={(values) =>
        setFilters((current) => ({ ...current, triggers: values }))
      }
      onReset={props.onReset}
    />
  );
}
