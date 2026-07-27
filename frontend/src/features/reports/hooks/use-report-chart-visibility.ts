import { useEffect, useState } from "react";

export const REPORT_CHART_DEFINITIONS = [
  { id: "costByDay", labelKey: "reports.charts.costByDay" },
  { id: "tokensByDay", labelKey: "reports.charts.tokensByDay" },
  { id: "timeToFirstToken", labelKey: "reports.charts.timeToFirstToken" },
  { id: "tokensPerSecond", labelKey: "reports.charts.tokensPerSecond" },
  { id: "queueWait", labelKey: "reports.charts.queueWait" },
] as const;

export type ReportChartId = (typeof REPORT_CHART_DEFINITIONS)[number]["id"];

export const REPORT_CHART_VISIBILITY_STORAGE_KEY =
  "codex-lb-reports-visible-charts";

const REPORT_CHART_IDS = REPORT_CHART_DEFINITIONS.map(({ id }) => id);

function allReportChartIds(): ReportChartId[] {
  return [...REPORT_CHART_IDS];
}

export function normalizeReportChartIds(
  ids: readonly string[],
): ReportChartId[] {
  const requestedIds = new Set(ids);
  return REPORT_CHART_IDS.filter((id) => requestedIds.has(id));
}

function readInitialReportChartIds(): ReportChartId[] {
  try {
    const storedValue = localStorage.getItem(
      REPORT_CHART_VISIBILITY_STORAGE_KEY,
    );
    if (storedValue === null) {
      return allReportChartIds();
    }

    const parsedValue: unknown = JSON.parse(storedValue);
    if (
      !Array.isArray(parsedValue) ||
      !parsedValue.every((id): id is string => typeof id === "string")
    ) {
      return allReportChartIds();
    }

    return normalizeReportChartIds(parsedValue);
  } catch {
    return allReportChartIds();
  }
}

export function useReportChartVisibility(): {
  visibleChartIds: ReportChartId[];
  setVisibleChartIds: (ids: readonly string[]) => void;
} {
  const [visibleChartIds, setVisibleChartIdsState] = useState<ReportChartId[]>(
    readInitialReportChartIds,
  );

  useEffect(() => {
    try {
      localStorage.setItem(
        REPORT_CHART_VISIBILITY_STORAGE_KEY,
        JSON.stringify(visibleChartIds),
      );
    } catch {
      // Storage failures should not prevent the in-memory preference from updating.
    }
  }, [visibleChartIds]);

  const setVisibleChartIds = (ids: readonly string[]) => {
    setVisibleChartIdsState(normalizeReportChartIds(ids));
  };

  return { visibleChartIds, setVisibleChartIds };
}
