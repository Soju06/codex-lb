import { act, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { renderWithProviders } from "@/test/utils";
import type { ReportsResponse } from "@/features/reports/schemas";
import { listAccounts } from "@/features/accounts/api";
import { getBrowserReportsTimeZone } from "@/features/reports/date";
import { useReports } from "@/features/reports/hooks/use-reports";
import { ReportsPage } from "./reports-page";

vi.mock("@/features/accounts/api", () => ({
  listAccounts: vi.fn().mockResolvedValue({ accounts: [] }),
}));

vi.mock("@/features/reports/hooks/use-reports", () => ({
  useReports: vi.fn(),
}));

vi.mock("@/features/reports/date", async () => {
  const actual = await vi.importActual<typeof import("@/features/reports/date")>(
    "@/features/reports/date",
  );
  return {
    ...actual,
    getBrowserReportsTimeZone: vi.fn(),
  };
});

vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) => (
      <div style={{ width: 400, height: 200 }}>{children}</div>
    ),
  };
});

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

const useReportsMock = vi.mocked(useReports);
const listAccountsMock = vi.mocked(listAccounts);
const getBrowserReportsTimeZoneMock = vi.mocked(getBrowserReportsTimeZone);
type UseReportsMockResult = ReturnType<typeof useReports>;
const REPORTS_TIMEZONE_STORAGE_KEY = "codex-lb-reports-timezone";

const asUseReportsResult = (
  value: Partial<UseReportsMockResult>,
): UseReportsMockResult => value as unknown as UseReportsMockResult;

describe("ReportsPage", () => {
  beforeEach(() => {
    useReportsMock.mockReset();
    listAccountsMock.mockReset();
    getBrowserReportsTimeZoneMock.mockReset();
    window.localStorage.clear();
    listAccountsMock.mockResolvedValue({ accounts: [] });
    getBrowserReportsTimeZoneMock.mockReturnValue("America/Los_Angeles");
  });

  afterEach(() => {
    vi.useRealTimers();
    window.localStorage.clear();
  });

  it("initializes default dates when the reports page mounts", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2030-01-15T12:00:00Z"));
    useReportsMock.mockReturnValue(
      asUseReportsResult({
        data: EMPTY_REPORT,
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    );

    renderWithProviders(<ReportsPage />);

    expect(useReportsMock.mock.calls[0]?.[0]).toMatchObject({
      startDate: "2030-01-09",
      endDate: "2030-01-15",
    });
  });

  it("passes the page-managed timezone state into both reports queries", () => {
    useReportsMock.mockReturnValue(
      asUseReportsResult({
        data: EMPTY_REPORT,
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    );

    renderWithProviders(<ReportsPage />);

    expect(useReportsMock).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({
        startDate: expect.any(String),
        endDate: expect.any(String),
      }),
      "America/Los_Angeles",
    );
    expect(useReportsMock).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({
        startDate: expect.any(String),
        endDate: expect.any(String),
        model: "",
        useragent: "",
      }),
      "America/Los_Angeles",
    );
  });

  it("uses the live valid timezone for reports queries even when a cached value exists", async () => {
    window.localStorage.setItem(REPORTS_TIMEZONE_STORAGE_KEY, "UTC");
    getBrowserReportsTimeZoneMock.mockReset();
    const actualDate = await vi.importActual<typeof import("@/features/reports/date")>(
      "@/features/reports/date",
    );
    getBrowserReportsTimeZoneMock.mockImplementation(actualDate.getBrowserReportsTimeZone);
    useReportsMock.mockReturnValue(
      asUseReportsResult({
        data: EMPTY_REPORT,
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    );

    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      locale: "en-US",
      calendar: "gregory",
      numberingSystem: "latn",
      timeZone: "America/Los_Angeles",
    });

    renderWithProviders(<ReportsPage />);

    expect(useReportsMock).toHaveBeenNthCalledWith(1, expect.any(Object), "America/Los_Angeles");
    expect(useReportsMock).toHaveBeenNthCalledWith(2, expect.any(Object), "America/Los_Angeles");
  });

  it("uses the cached valid timezone for reports queries when live detection is unavailable", async () => {
    window.localStorage.setItem(REPORTS_TIMEZONE_STORAGE_KEY, "Europe/Paris");
    getBrowserReportsTimeZoneMock.mockReset();
    const actualDate = await vi.importActual<typeof import("@/features/reports/date")>(
      "@/features/reports/date",
    );
    getBrowserReportsTimeZoneMock.mockImplementation(actualDate.getBrowserReportsTimeZone);
    useReportsMock.mockReturnValue(
      asUseReportsResult({
        data: EMPTY_REPORT,
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    );

    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      locale: "en-US",
      calendar: "gregory",
      numberingSystem: "latn",
      timeZone: undefined as unknown as string,
    });

    renderWithProviders(<ReportsPage />);

    expect(useReportsMock).toHaveBeenNthCalledWith(1, expect.any(Object), "Europe/Paris");
    expect(useReportsMock).toHaveBeenNthCalledWith(2, expect.any(Object), "Europe/Paris");
  });

  it("omits timezone for reports queries only when live and cached timezones are both invalid", async () => {
    window.localStorage.setItem(REPORTS_TIMEZONE_STORAGE_KEY, "Moon/BaseAlpha");
    getBrowserReportsTimeZoneMock.mockReset();
    const actualDate = await vi.importActual<typeof import("@/features/reports/date")>(
      "@/features/reports/date",
    );
    getBrowserReportsTimeZoneMock.mockImplementation(actualDate.getBrowserReportsTimeZone);
    useReportsMock.mockReturnValue(
      asUseReportsResult({
        data: EMPTY_REPORT,
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    );

    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      locale: "en-US",
      calendar: "gregory",
      numberingSystem: "latn",
      timeZone: "Mars/Olympus",
    });

    renderWithProviders(<ReportsPage />);

    expect(useReportsMock).toHaveBeenNthCalledWith(1, expect.any(Object), undefined);
    expect(useReportsMock).toHaveBeenNthCalledWith(2, expect.any(Object), undefined);
  });

  it("refreshes timezone state on focus, visibility changes, and interval ticks", async () => {
    vi.useFakeTimers();
    useReportsMock.mockReturnValue(
      asUseReportsResult({
        data: EMPTY_REPORT,
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    );
    getBrowserReportsTimeZoneMock.mockReturnValueOnce("America/Los_Angeles");

    renderWithProviders(<ReportsPage />);

    expect(useReportsMock).toHaveBeenNthCalledWith(
      1,
      expect.any(Object),
      "America/Los_Angeles",
    );
    expect(useReportsMock).toHaveBeenNthCalledWith(
      2,
      expect.any(Object),
      "America/Los_Angeles",
    );

    getBrowserReportsTimeZoneMock.mockReturnValue("America/New_York");
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });

    expect(useReportsMock).toHaveBeenNthCalledWith(
      3,
      expect.any(Object),
      "America/New_York",
    );
    expect(useReportsMock).toHaveBeenNthCalledWith(
      4,
      expect.any(Object),
      "America/New_York",
    );

    getBrowserReportsTimeZoneMock.mockReturnValue(undefined);
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    expect(useReportsMock).toHaveBeenNthCalledWith(
      5,
      expect.any(Object),
      undefined,
    );
    expect(useReportsMock).toHaveBeenNthCalledWith(
      6,
      expect.any(Object),
      undefined,
    );

    getBrowserReportsTimeZoneMock.mockReturnValue("America/Chicago");
    act(() => {
      vi.advanceTimersByTime(60_000);
    });

    expect(useReportsMock).toHaveBeenNthCalledWith(
      7,
      expect.any(Object),
      "America/Chicago",
    );
    expect(useReportsMock).toHaveBeenNthCalledWith(
      8,
      expect.any(Object),
      "America/Chicago",
    );
  });

  it("keeps model options from the unfiltered model catalog", async () => {
    const user = userEvent.setup();
    useReportsMock.mockImplementation((filters) =>
      asUseReportsResult({
        data: {
          ...EMPTY_REPORT,
          byModel: filters.model
            ? [{ model: "gpt-5.1", costUsd: 1, requests: 1, percentage: 100 }]
              : [
                  { model: "gpt-5.1", costUsd: 1, requests: 1, percentage: 50 },
                  { model: "gpt-5.2", costUsd: 1, requests: 1, percentage: 50 },
                ],
          byUseragent: filters.model
            ? [{ useragent: "CLI", costUsd: 1, requests: 1, percentage: 100 }]
            : [
                { useragent: "CLI", costUsd: 1, requests: 1, percentage: 50 },
                { useragent: "SDK", costUsd: 1, requests: 1, percentage: 50 },
              ],
        },
        isLoading: false,
      }),
    );

    renderWithProviders(<ReportsPage initialFilters={{ model: "gpt-5.1" }} />);

    await user.click(
      screen.getByRole("button", { name: /gpt-5.1/i, expanded: false }),
    );

    expect(
      await screen.findByRole("menuitemcheckbox", { name: /gpt-5.2/i }),
    ).toBeInTheDocument();
  });

  it("keeps user-agent options from the relaxed reports catalog", async () => {
    const user = userEvent.setup();
    useReportsMock.mockImplementation((filters) =>
      asUseReportsResult({
        data: {
          ...EMPTY_REPORT,
          byModel: [
            { model: "gpt-5.1", costUsd: 1, requests: 1, percentage: 100 },
          ],
          byUseragent: filters.useragent
            ? [{ useragent: "CLI", costUsd: 1, requests: 1, percentage: 100 }]
            : [
                { useragent: "CLI", costUsd: 1, requests: 1, percentage: 50 },
                { useragent: "SDK", costUsd: 1, requests: 1, percentage: 50 },
              ],
        },
        isLoading: false,
      }),
    );

    renderWithProviders(<ReportsPage initialFilters={{ useragent: "CLI" }} />);

    await user.click(
      screen.getByRole("button", { name: /^CLI$/i, expanded: false }),
    );

    expect(
      await screen.findByRole("menuitemcheckbox", { name: /^SDK$/i }),
    ).toBeInTheDocument();
  });

  it("clears the last clicked preset highlight after manual date edits", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2030-01-15T12:00:00Z"));

    useReportsMock.mockReturnValue(
      asUseReportsResult({
        data: EMPTY_REPORT,
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    );

    const { container } = renderWithProviders(<ReportsPage />);

    const button7d = screen.getByRole("button", { name: "7d" });
    const button30d = screen.getByRole("button", { name: "30d" });

    expect(button7d).toHaveAttribute("aria-pressed", "true");
    expect(button30d).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(button30d);

    expect(button30d).toHaveAttribute("aria-pressed", "true");
    expect(useReportsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        startDate: "2029-12-17",
        endDate: "2030-01-15",
      }),
      "America/Los_Angeles",
    );

    const [startDateInput] = container.querySelectorAll<HTMLInputElement>('input[type="date"]');
    fireEvent.change(startDateInput, { target: { value: "2030-01-01" } });

    expect(button30d).toHaveAttribute("aria-pressed", "false");
    expect(useReportsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        startDate: "2030-01-01",
        endDate: "2030-01-15",
      }),
      "America/Los_Angeles",
    );
  });

  it("shows an error when report loading fails", async () => {
    useReportsMock.mockImplementation((filters) =>
      filters.model
        ? asUseReportsResult({
            isLoading: false,
            isError: true,
            error: new Error("report API unavailable"),
            refetch: vi.fn(),
            data: null as unknown as ReportsResponse,
          })
        : asUseReportsResult({
            data: EMPTY_REPORT,
            isLoading: false,
            isError: false,
            refetch: vi.fn(),
          }),
    );

    renderWithProviders(<ReportsPage initialFilters={{ model: "gpt-5.1" }} />);

    expect(
      await screen.findByText(
        /Failed to load report data: report API unavailable/i,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("shows one shared catalog failure for model and user-agent options", async () => {
    useReportsMock.mockImplementation((filters) =>
      filters.model || filters.useragent
        ? asUseReportsResult({
            data: {
              ...EMPTY_REPORT,
              byModel: [
                { model: "gpt-5.1", costUsd: 1, requests: 1, percentage: 100 },
              ],
              byUseragent: [
                { useragent: "CLI", costUsd: 1, requests: 1, percentage: 100 },
              ],
            },
            isLoading: false,
            isError: false,
            refetch: vi.fn(),
          })
        : asUseReportsResult({
            isLoading: false,
            isError: true,
            error: new Error("shared catalog endpoint unavailable"),
            refetch: vi.fn(),
            data: undefined,
          }),
    );

    renderWithProviders(
      <ReportsPage initialFilters={{ model: "gpt-5.1", useragent: "CLI" }} />,
    );

    expect(
      await screen.findByText(
        /Failed to load model and user-agent options: shared catalog endpoint unavailable/i,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /gpt-5.1/i, expanded: false }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /^CLI$/i, expanded: false }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/Failed to load model options:/i),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Failed to load user-agent options:/i),
    ).not.toBeInTheDocument();
  });

  it("renders an independent user-agent distribution card below the model card", async () => {
    useReportsMock.mockImplementation(() =>
      asUseReportsResult({
        data: {
          ...EMPTY_REPORT,
          byModel: [{ model: "gpt-5.1", costUsd: 12, requests: 3, percentage: 100 }],
          byUseragent: [
            { useragent: "CLI", costUsd: 10, requests: 2, percentage: 100 },
          ],
        },
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    );

    renderWithProviders(<ReportsPage />);

    const modelCard = await screen.findByText("Distribution by Model");
    const useragentCard = await screen.findByText("Distribution by UserAgent");

    expect(modelCard.compareDocumentPosition(useragentCard)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("keeps the model and user-agent metric toggles independent", async () => {
    const user = userEvent.setup();

    useReportsMock.mockImplementation(() =>
      asUseReportsResult({
        data: {
          ...EMPTY_REPORT,
          byModel: [
            { model: "gpt-5.1", costUsd: 12, requests: 3, percentage: 100 },
          ],
          byUseragent: [
            { useragent: "CLI", costUsd: 10, requests: 2, percentage: 100 },
          ],
        },
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    );

    renderWithProviders(<ReportsPage />);

    const modelCard = (await screen.findByText("Distribution by Model")).closest("div.rounded-xl.border.bg-card.p-5");
    const useragentCard = (await screen.findByText("Distribution by UserAgent")).closest("div.rounded-xl.border.bg-card.p-5");

    expect(modelCard).not.toBeNull();
    expect(useragentCard).not.toBeNull();

    await user.click(within(useragentCard as HTMLElement).getByRole("button", { name: /^req$/i }));

    expect(within(modelCard as HTMLElement).getByRole("button", { name: /^cost$/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(useragentCard as HTMLElement).getByRole("button", { name: /^req$/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("shows account option load failures instead of hiding empty selector silently", async () => {
    useReportsMock.mockImplementation(() =>
      asUseReportsResult({
        data: EMPTY_REPORT,
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      }),
    );
    listAccountsMock.mockRejectedValueOnce(
      new Error("accounts backend timeout"),
    );

    renderWithProviders(<ReportsPage />);

    expect(
      await screen.findByText(
        /Failed to load account options: accounts backend timeout/i,
      ),
    ).toBeInTheDocument();
    expect(
      screen
        .getAllByRole("button", { name: /accounts/i })
        .find((button) => button.getAttribute("aria-haspopup") === "menu"),
    ).toBeInTheDocument();
  });
});
