import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import App from "@/App";
import { daysAgoLocalISO } from "@/features/reports/date";
import type { ReportsResponse } from "@/features/reports/schemas";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

const EMPTY_REPORT: ReportsResponse = {
  summary: {
    totalCostUsd: 0,
    totalInputTokens: 0,
    totalOutputTokens: 0,
    totalCachedTokens: 0,
    totalRequests: 0,
    totalErrors: 0,
    activeAccounts: 0,
    avgCostPerDay: 0,
    avgRequestsPerDay: 0,
  },
  comparison: {
    canCompare: false,
    previous: {
      totalCostUsd: 0,
      totalTokens: 0,
      totalRequests: 0,
    },
  },
  daily: [],
  byModel: [],
  byUseragent: [],
  byAccount: [],
};

const REPORT_WITH_MODEL: ReportsResponse = {
  ...EMPTY_REPORT,
  byModel: [
    {
      model: "gpt-5.1",
      costUsd: 0,
      requests: 0,
      percentage: 100,
    },
  ],
};

const CORRECTIONS = [
  {
    name: "start date",
    label: "Start date",
    correctedDaysAgo: 9,
    expectedStartDaysAgo: 9,
    expectedEndDaysAgo: 8,
  },
  {
    name: "end date",
    label: "End date",
    correctedDaysAgo: 6,
    expectedStartDaysAgo: 7,
    expectedEndDaysAgo: 6,
  },
] as const;

describe("reports date-range flow integration", () => {
  it.each(CORRECTIONS)(
    "drives the App reports route, suppresses inverted requests, and recovers through the $name",
    async ({ label, correctedDaysAgo, expectedStartDaysAgo, expectedEndDaysAgo }) => {
      const user = userEvent.setup({ delay: null });
      const referenceDate = new Date();
      const invertedStart = daysAgoLocalISO(7, referenceDate);
      const invertedEnd = daysAgoLocalISO(8, referenceDate);
      const expectedStart = daysAgoLocalISO(expectedStartDaysAgo, referenceDate);
      const expectedEnd = daysAgoLocalISO(expectedEndDaysAgo, referenceDate);
      const reportsRequests: URLSearchParams[] = [];
      let accountRequests = 0;

      server.use(
        http.get("/api/accounts", () => {
          accountRequests += 1;
          return HttpResponse.json({ accounts: [] });
        }),
        http.get("/api/reports", ({ request }) => {
          reportsRequests.push(new URL(request.url).searchParams);
          return HttpResponse.json(REPORT_WITH_MODEL);
        }),
      );

      window.history.pushState({}, "", "/reports");
      renderWithProviders(<App />);

      expect(window.location.pathname).toBe("/reports");
      await waitFor(() => {
        expect(accountRequests).toBeGreaterThan(0);
        expect(reportsRequests.length).toBeGreaterThan(0);
      });
      expect(await screen.findByRole("heading", { name: "Cost Report" })).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Model", expanded: false }));
      await user.click(
        await screen.findByRole("menuitemcheckbox", { name: "gpt-5.1" }),
      );
      await user.keyboard("{Escape}");
      await waitFor(() => {
        expect(
          reportsRequests.some((params) => params.get("model") === "gpt-5.1"),
        ).toBe(true);
      });

      const startInput = screen.getByLabelText("Start date");
      const endInput = screen.getByLabelText("End date");
      fireEvent.change(endInput, { target: { value: invertedEnd } });
      fireEvent.change(startInput, { target: { value: invertedStart } });

      await waitFor(() => {
        expect(startInput).toHaveAttribute("aria-invalid", "true");
        expect(endInput).toHaveAttribute("aria-invalid", "true");
      });
      expect(
        screen.getByText("Start date must be on or before end date."),
      ).toBeInTheDocument();
      expect(
        reportsRequests.filter((params) => {
          const startDate = params.get("start_date");
          const endDate = params.get("end_date");
          return startDate !== null && endDate !== null && startDate > endDate;
        }),
      ).toHaveLength(0);

      fireEvent.change(screen.getByLabelText(label), {
        target: { value: daysAgoLocalISO(correctedDaysAgo, referenceDate) },
      });

      await waitFor(() => {
        const correctedRequests = reportsRequests.filter(
          (params) =>
            params.get("start_date") === expectedStart &&
            params.get("end_date") === expectedEnd,
        );
        expect(correctedRequests).toHaveLength(2);
      });
      const correctedRequests = reportsRequests.filter(
        (params) =>
          params.get("start_date") === expectedStart &&
          params.get("end_date") === expectedEnd,
      );
      expect(
        correctedRequests
          .map((params) => params.get("model") ?? "")
          .sort(),
      ).toEqual(["", "gpt-5.1"]);
      expect(
        screen.queryByText("Start date must be on or before end date."),
      ).not.toBeInTheDocument();
      expect(startInput).not.toHaveAttribute("aria-invalid");
      expect(endInput).not.toHaveAttribute("aria-invalid");
      expect(startInput).not.toHaveAttribute("aria-describedby");
      expect(endInput).not.toHaveAttribute("aria-describedby");
    },
  );

  it("retries Accounts without refetching either Reports query while the range is inverted", async () => {
    const user = userEvent.setup({ delay: null });
    const referenceDate = new Date();
    const reportsRequests: URLSearchParams[] = [];
    let accountRequests = 0;

    server.use(
      http.get("/api/accounts", () => {
        accountRequests += 1;
        return HttpResponse.json(
          {
            error: {
              code: "accounts_unavailable",
              message: "Accounts unavailable",
            },
          },
          { status: 503 },
        );
      }),
      http.get("/api/reports", ({ request }) => {
        reportsRequests.push(new URL(request.url).searchParams);
        return HttpResponse.json(REPORT_WITH_MODEL);
      }),
    );

    window.history.pushState({}, "", "/reports");
    renderWithProviders(<App />);

    expect(window.location.pathname).toBe("/reports");
    await waitFor(() => {
      expect(accountRequests).toBeGreaterThan(0);
      expect(reportsRequests.length).toBeGreaterThan(0);
    });
    expect(await screen.findByRole("heading", { name: "Cost Report" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Model", expanded: false }));
    await user.click(
      await screen.findByRole("menuitemcheckbox", { name: "gpt-5.1" }),
    );
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(
        reportsRequests.some((params) => params.get("model") === "gpt-5.1"),
      ).toBe(true);
    });

    fireEvent.change(screen.getByLabelText("End date"), {
      target: { value: daysAgoLocalISO(8, referenceDate) },
    });
    fireEvent.change(screen.getByLabelText("Start date"), {
      target: { value: daysAgoLocalISO(7, referenceDate) },
    });

    expect(
      await screen.findByText("Start date must be on or before end date."),
    ).toBeInTheDocument();
    const retryButton = await screen.findByRole("button", { name: "Retry" });
    const accountRequestsBeforeRetry = accountRequests;
    const reportsRequestsBeforeRetry = reportsRequests.length;

    await user.click(retryButton);

    await waitFor(() => {
      expect(accountRequests).toBe(accountRequestsBeforeRetry + 1);
    });
    expect(reportsRequests).toHaveLength(reportsRequestsBeforeRetry);
    expect(
      reportsRequests.filter((params) => {
        const startDate = params.get("start_date");
        const endDate = params.get("end_date");
        return startDate !== null && endDate !== null && startDate > endDate;
      }),
    ).toHaveLength(0);
  });
});
