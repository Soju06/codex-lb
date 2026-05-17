import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import type { AccountDisplayEntry } from "@/features/automations/account-display";
import { RunDetailsDialog } from "@/features/automations/components/run-details-dialog";
import type { AutomationRunDetails } from "@/features/automations/schemas";
import { useTimeFormatStore } from "@/hooks/use-time-format";

function createDetailsData(): AutomationRunDetails {
  return {
    run: {
      id: "run-1",
      jobId: "job-1",
      jobName: "Daily refresh",
      model: "gpt-5.4-mini",
      reasoningEffort: "low",
      trigger: "scheduled",
      status: "partial",
      effectiveStatus: "running",
      scheduledFor: "2026-04-19T05:00:00Z",
      startedAt: "2026-04-19T05:00:00Z",
      finishedAt: null,
      accountId: null,
      errorCode: null,
      errorMessage: null,
      attemptCount: 1,
      totalAccounts: 4,
      completedAccounts: 2,
      pendingAccounts: 2,
      cycleKey: "scheduled:job-1:cycle-1",
    },
    accounts: [
      {
        accountId: "acc-1",
        status: "success",
        runId: "row-1",
        scheduledFor: "2026-04-19T05:00:00Z",
        startedAt: "2026-04-19T05:00:05Z",
        finishedAt: "2026-04-19T05:00:20Z",
        errorCode: null,
        errorMessage: null,
      },
      {
        accountId: "acc-2",
        status: "partial",
        runId: "row-2",
        scheduledFor: null,
        startedAt: "2026-04-19T05:00:10Z",
        finishedAt: null,
        errorCode: "rate_limited",
        errorMessage: "try later",
      },
      {
        accountId: "acc-3",
        status: "running",
        runId: "row-3",
        scheduledFor: "2026-04-19T05:00:40Z",
        startedAt: "2026-04-19T05:00:40Z",
        finishedAt: null,
        errorCode: null,
        errorMessage: null,
      },
      {
        accountId: "acc-4",
        status: "pending",
        runId: null,
        scheduledFor: "2026-04-19T05:01:10Z",
        startedAt: null,
        finishedAt: null,
        errorCode: null,
        errorMessage: null,
      },
    ],
    totalAccounts: 4,
    completedAccounts: 2,
    pendingAccounts: 2,
  };
}

describe("RunDetailsDialog", () => {
  beforeEach(() => {
    useTimeFormatStore.setState({ timeFormat: "24h" });
  });

  it("renders loading and unavailable states", () => {
    const { rerender } = render(
      <RunDetailsDialog
        open
        onOpenChange={() => {}}
        isLoading
        data={undefined}
        blurred={false}
        accountDisplayIndex={new Map<string, AccountDisplayEntry>()}
        accountBlurIndex={new Map()}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Loading...");

    rerender(
      <RunDetailsDialog
        open
        onOpenChange={() => {}}
        isLoading={false}
        data={undefined}
        blurred={false}
        accountDisplayIndex={new Map<string, AccountDisplayEntry>()}
        accountBlurIndex={new Map()}
      />,
    );

    expect(screen.getByText("Run details unavailable.")).toBeInTheDocument();
  });

  it("renders mixed run details with account states, errors, and privacy blur", () => {
    const data = createDetailsData();
    const accountDisplayIndex = new Map<string, AccountDisplayEntry>([
      [
        "acc-1",
        {
          accountId: "acc-1",
          primary: "Alice",
          secondary: "alice@example.com",
          title: "Alice\nalice@example.com",
        },
      ],
      [
        "acc-2",
        {
          accountId: "acc-2",
          primary: "Bob",
          secondary: null,
          title: "Bob",
        },
      ],
      [
        "acc-3",
        {
          accountId: "acc-3",
          primary: "Team Gamma",
          secondary: "gamma@example.com",
          title: "Team Gamma\ngamma@example.com",
        },
      ],
    ]);
    const accountBlurIndex = new Map([
      ["acc-1", { primary: true, secondary: true, any: true }],
    ]);

    render(
      <RunDetailsDialog
        open
        onOpenChange={() => {}}
        isLoading={false}
        data={data}
        blurred
        accountDisplayIndex={accountDisplayIndex}
        accountBlurIndex={accountBlurIndex}
      />,
    );

    expect(screen.getByRole("heading", { name: "Run details" })).toBeInTheDocument();
    expect(screen.getByText("in progress")).toBeInTheDocument();
    expect(screen.getByText("Completed 2 of 4")).toBeInTheDocument();
    expect(screen.getByText("Includes 1 partial")).toBeInTheDocument();
    expect(screen.getByText("1 running")).toBeInTheDocument();

    const bobRow = screen.getByText("Bob").closest("tr");
    expect(bobRow).not.toBeNull();
    if (!bobRow) {
      throw new Error("Bob row not found");
    }
    expect(within(bobRow).getByText("rate_limited")).toBeInTheDocument();
    expect(within(bobRow).getByText("try later")).toBeInTheDocument();
    expect(within(bobRow).getAllByText("-").length).toBeGreaterThan(0);

    expect(screen.getByText("Unknown account")).toBeInTheDocument();
    expect(screen.getAllByText("No error").length).toBeGreaterThan(0);
    expect(document.querySelectorAll(".privacy-blur").length).toBeGreaterThan(0);
  });
});
