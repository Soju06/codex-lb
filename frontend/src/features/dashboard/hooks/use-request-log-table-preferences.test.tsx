import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import {
  REQUEST_LOG_TABLE_PREFERENCES_STORAGE_KEY,
  useRequestLogTablePreferences,
} from "@/features/dashboard/hooks/use-request-log-table-preferences";
import {
  ALL_REQUEST_LOG_COLUMNS,
  MAX_REQUEST_LOG_COLUMN_WIDTH,
  MIN_REQUEST_LOG_COLUMN_WIDTH,
} from "@/features/dashboard/request-log-columns";

describe("useRequestLogTablePreferences", () => {
  beforeEach(() => {
    window.localStorage.removeItem(REQUEST_LOG_TABLE_PREFERENCES_STORAGE_KEY);
  });

  it("shows every request-log column by default", () => {
    const { result } = renderHook(() => useRequestLogTablePreferences());

    expect(result.current.visibleColumns).toEqual(ALL_REQUEST_LOG_COLUMNS);
    expect(result.current.columnWidths).toEqual({});
  });

  it("persists visible columns and individual widths across remounts", () => {
    const { result, unmount } = renderHook(() => useRequestLogTablePreferences());

    act(() => {
      result.current.toggleColumn("plan");
      result.current.setColumnWidth("account", 284);
    });

    expect(result.current.visibleColumns).not.toContain("plan");
    expect(result.current.columnWidths.account).toBe(284);

    unmount();
    const restored = renderHook(() => useRequestLogTablePreferences());
    expect(restored.result.current.visibleColumns).not.toContain("plan");
    expect(restored.result.current.columnWidths.account).toBe(284);
  });

  it("clamps finite widths and ignores malformed width entries", () => {
    window.localStorage.setItem(
      REQUEST_LOG_TABLE_PREFERENCES_STORAGE_KEY,
      JSON.stringify({
        visibleColumns: ["time", "account"],
        columnWidths: {
          time: 1,
          account: "wide",
          details: 10_000,
          unknown: 200,
        },
      }),
    );

    const { result } = renderHook(() => useRequestLogTablePreferences());

    expect(result.current.columnWidths).toEqual({
      time: MIN_REQUEST_LOG_COLUMN_WIDTH,
      details: MAX_REQUEST_LOG_COLUMN_WIDTH,
    });
  });

  it("keeps the final visible column selected", () => {
    window.localStorage.setItem(
      REQUEST_LOG_TABLE_PREFERENCES_STORAGE_KEY,
      JSON.stringify({ visibleColumns: ["time"], columnWidths: {} }),
    );
    const { result } = renderHook(() => useRequestLogTablePreferences());

    act(() => {
      result.current.toggleColumn("time");
    });

    expect(result.current.visibleColumns).toEqual(["time"]);
  });

  it.each([
    "{not-json",
    JSON.stringify({ visibleColumns: [], columnWidths: {} }),
    JSON.stringify({ visibleColumns: ["time", "retired-column"], columnWidths: {} }),
  ])("falls back safely for malformed or stale preferences", (stored) => {
    window.localStorage.setItem(REQUEST_LOG_TABLE_PREFERENCES_STORAGE_KEY, stored);

    const { result } = renderHook(() => useRequestLogTablePreferences());

    expect(result.current.visibleColumns).toEqual(ALL_REQUEST_LOG_COLUMNS);
  });

  it("restores default widths too when stored visibility is stale", () => {
    window.localStorage.setItem(
      REQUEST_LOG_TABLE_PREFERENCES_STORAGE_KEY,
      JSON.stringify({
        visibleColumns: ["time", "retired-column"],
        columnWidths: { time: 240, account: 320 },
      }),
    );

    const { result } = renderHook(() => useRequestLogTablePreferences());

    expect(result.current.visibleColumns).toEqual(ALL_REQUEST_LOG_COLUMNS);
    expect(result.current.columnWidths).toEqual({});
  });

  it("restores all columns, clears widths, and removes stored customization", () => {
    const { result } = renderHook(() => useRequestLogTablePreferences());
    act(() => {
      result.current.toggleColumn("plan");
      result.current.setColumnWidth("account", 240);
    });
    act(() => {
      result.current.restoreDefaultLayout();
    });

    expect(result.current.visibleColumns).toEqual(ALL_REQUEST_LOG_COLUMNS);
    expect(result.current.columnWidths).toEqual({});
    expect(
      window.localStorage.getItem(REQUEST_LOG_TABLE_PREFERENCES_STORAGE_KEY),
    ).toBeNull();
  });
});
