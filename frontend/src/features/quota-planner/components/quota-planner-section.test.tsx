import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { QuotaPlannerSection } from "@/features/quota-planner/components/quota-planner-section";
import { useDateDisplayFormatStore, type DateDisplayFormat } from "@/hooks/use-date-format";
import {
  createQuotaPlannerDecision,
  createQuotaPlannerSettings,
} from "@/test/mocks/factories";
import { formatTimeLong } from "@/utils/formatters";

const hookMocks = vi.hoisted(() => ({
  useQuotaPlanner: vi.fn(),
}));

vi.mock("@/features/quota-planner/hooks/use-quota-planner", () => hookMocks);

const TARGET_PEAK_AT = "2026-08-09T14:30:45";
let decision: ReturnType<typeof createQuotaPlannerDecision>;

function formatPeakLabel(displayFormat: DateDisplayFormat): string {
  const formatted = formatTimeLong(TARGET_PEAK_AT, displayFormat);
  return `Peak ${formatted.date} ${formatted.time}`;
}

describe("QuotaPlannerSection", () => {
  beforeEach(() => {
    useDateDisplayFormatStore.setState({ dateDisplayFormat: "default" });
    const queryState = {
      error: null,
      isFetching: false,
      isLoading: false,
      isPending: false,
      isSuccess: true,
      refetch: vi.fn(),
    };
    const mutationState = {
      error: null,
      isPending: false,
      mutate: vi.fn(),
    };
    decision = createQuotaPlannerDecision({
      createdAt: "2026-08-09T12:00:00",
      scheduledAt: null,
      details: { target_peak_at: TARGET_PEAK_AT },
    });

    hookMocks.useQuotaPlanner.mockReturnValue({
      settingsQuery: {
        ...queryState,
        data: createQuotaPlannerSettings(),
      },
      decisionsQuery: {
        ...queryState,
        data: [decision],
      },
      forecastQuery: {
        ...queryState,
        data: null,
      },
      updateSettingsMutation: mutationState,
      warmNowMutation: mutationState,
      cancelDecisionMutation: mutationState,
    });
  });

  it("updates a decision Peak label when the date display format changes", () => {
    render(<QuotaPlannerSection />);

    expect(screen.getByText(formatPeakLabel("default"))).toBeInTheDocument();

    act(() => {
      useDateDisplayFormatStore.setState({ dateDisplayFormat: "iso8601" });
    });

    expect(screen.getByText(formatPeakLabel("iso8601"))).toBeInTheDocument();

    act(() => {
      useDateDisplayFormatStore.setState({ dateDisplayFormat: "default" });
    });

    expect(screen.getByText(formatPeakLabel("default"))).toBeInTheDocument();
    expect(decision.details?.target_peak_at).toBe(TARGET_PEAK_AT);
  });
});
