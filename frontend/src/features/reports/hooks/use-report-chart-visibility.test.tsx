import { renderHook, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  REPORT_CHART_DEFINITIONS,
  REPORT_CHART_VISIBILITY_STORAGE_KEY,
  useReportChartVisibility,
} from "./use-report-chart-visibility";

const KEY = REPORT_CHART_VISIBILITY_STORAGE_KEY;
const ALL_IDS = REPORT_CHART_DEFINITIONS.map(({ id }) => id);

describe("useReportChartVisibility", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("defaults to all five charts and writes the default", () => {
    const { result } = renderHook(() => useReportChartVisibility());

    expect(result.current.visibleChartIds).toEqual(ALL_IDS);
    expect(JSON.parse(localStorage.getItem(KEY) ?? "null")).toEqual(ALL_IDS);
  });

  it("restores known IDs in canonical order and removes duplicates", () => {
    localStorage.setItem(
      KEY,
      JSON.stringify(["queueWait", "costByDay", "costByDay"]),
    );

    const { result } = renderHook(() => useReportChartVisibility());

    expect(result.current.visibleChartIds).toEqual(["costByDay", "queueWait"]);
  });

  it("discards unknown IDs and preserves an empty array", () => {
    localStorage.setItem(
      KEY,
      JSON.stringify(["unknown", "tokensPerSecond"]),
    );
    const subset = renderHook(() => useReportChartVisibility());

    expect(subset.result.current.visibleChartIds).toEqual(["tokensPerSecond"]);

    subset.unmount();
    localStorage.setItem(KEY, JSON.stringify([]));
    const empty = renderHook(() => useReportChartVisibility());

    expect(empty.result.current.visibleChartIds).toEqual([]);
  });

  it("falls back for malformed and structurally invalid values", () => {
    for (const value of [
      "not-json",
      JSON.stringify({}),
      JSON.stringify(["costByDay", 1]),
    ]) {
      localStorage.setItem(KEY, value);
      const { result, unmount } = renderHook(() => useReportChartVisibility());

      expect(result.current.visibleChartIds).toEqual(ALL_IDS);
      unmount();
    }
  });

  it("defaults to all charts when storage reads throw during initialization", () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(
      () => {
        throw new Error("storage unavailable");
      },
    );

    try {
      const { result } = renderHook(() => useReportChartVisibility());

      expect(result.current.visibleChartIds).toEqual(ALL_IDS);
    } finally {
      getItem.mockRestore();
    }
  });

  it("updates in memory when localStorage writes throw", () => {
    const { result } = renderHook(() => useReportChartVisibility());
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(
      () => {
        throw new Error("storage unavailable");
      },
    );

    act(() => result.current.setVisibleChartIds(["queueWait"]));

    expect(result.current.visibleChartIds).toEqual(["queueWait"]);
    setItem.mockRestore();
  });
});
