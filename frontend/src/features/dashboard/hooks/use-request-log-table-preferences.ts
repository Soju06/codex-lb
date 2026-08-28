import { useCallback, useState } from "react";

import {
  ALL_REQUEST_LOG_COLUMNS,
  DEFAULT_REQUEST_LOG_COLUMNS,
  clampRequestLogColumnWidth,
  type RequestLogColumnId,
  type RequestLogColumnWidths,
} from "@/features/dashboard/request-log-columns";

export const REQUEST_LOG_TABLE_PREFERENCES_STORAGE_KEY =
  "codex-lb-dashboard-request-log-columns:v1";

type RequestLogTablePreferences = {
  visibleColumns: RequestLogColumnId[];
  columnWidths: RequestLogColumnWidths;
};

const supportedColumns = new Set<RequestLogColumnId>(ALL_REQUEST_LOG_COLUMNS);

function isRequestLogColumnId(value: unknown): value is RequestLogColumnId {
  return typeof value === "string" && supportedColumns.has(value as RequestLogColumnId);
}

function normalizeColumnWidths(value: unknown): RequestLogColumnWidths {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }

  const stored = value as Record<string, unknown>;
  const widths: RequestLogColumnWidths = {};
  for (const column of ALL_REQUEST_LOG_COLUMNS) {
    const width = stored[column];
    if (typeof width === "number" && Number.isFinite(width)) {
      widths[column] = clampRequestLogColumnWidth(width);
    }
  }
  return widths;
}

function defaultPreferences(): RequestLogTablePreferences {
  return {
    visibleColumns: [...DEFAULT_REQUEST_LOG_COLUMNS],
    columnWidths: {},
  };
}

function normalizeVisibleColumns(value: unknown): RequestLogColumnId[] | null {
  if (
    !Array.isArray(value) ||
    value.length === 0 ||
    value.some((column) => !isRequestLogColumnId(column))
  ) {
    return null;
  }

  const selected = new Set(value);
  return ALL_REQUEST_LOG_COLUMNS.filter((column) => selected.has(column));
}

function loadPreferences(): RequestLogTablePreferences {
  if (typeof window === "undefined") {
    return defaultPreferences();
  }

  try {
    const stored = window.localStorage.getItem(REQUEST_LOG_TABLE_PREFERENCES_STORAGE_KEY);
    if (!stored) {
      return defaultPreferences();
    }

    const parsed = JSON.parse(stored) as {
      visibleColumns?: unknown;
      columnWidths?: unknown;
    };
    const visibleColumns = normalizeVisibleColumns(parsed.visibleColumns);
    if (visibleColumns === null) {
      // Stale or malformed visibility invalidates the whole stored layout so the
      // dashboard restores the complete default layout, widths included.
      return defaultPreferences();
    }
    return {
      visibleColumns,
      columnWidths: normalizeColumnWidths(parsed.columnWidths),
    };
  } catch {
    return defaultPreferences();
  }
}

function persistPreferences(preferences: RequestLogTablePreferences): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(
      REQUEST_LOG_TABLE_PREFERENCES_STORAGE_KEY,
      JSON.stringify(preferences),
    );
  } catch {
    // Browser storage may be unavailable in private or locked-down sessions.
  }
}

export function useRequestLogTablePreferences() {
  const [preferences, setPreferences] = useState<RequestLogTablePreferences>(loadPreferences);

  const toggleColumn = useCallback((column: RequestLogColumnId) => {
    setPreferences((current) => {
      const isVisible = current.visibleColumns.includes(column);
      if (isVisible && current.visibleColumns.length === 1) {
        return current;
      }

      const selected = new Set(current.visibleColumns);
      if (isVisible) {
        selected.delete(column);
      } else {
        selected.add(column);
      }
      const next = {
        ...current,
        visibleColumns: ALL_REQUEST_LOG_COLUMNS.filter((candidate) => selected.has(candidate)),
      };
      persistPreferences(next);
      return next;
    });
  }, []);

  const setColumnWidth = useCallback((column: RequestLogColumnId, width: number) => {
    setPreferences((current) => {
      const next = {
        ...current,
        columnWidths: {
          ...current.columnWidths,
          [column]: clampRequestLogColumnWidth(width),
        },
      };
      persistPreferences(next);
      return next;
    });
  }, []);

  const restoreDefaultLayout = useCallback(() => {
    const next = defaultPreferences();
    if (typeof window !== "undefined") {
      try {
        window.localStorage.removeItem(REQUEST_LOG_TABLE_PREFERENCES_STORAGE_KEY);
      } catch {
        // Browser storage may be unavailable in private or locked-down sessions.
      }
    }
    setPreferences(next);
  }, []);

  return {
    visibleColumns: preferences.visibleColumns,
    columnWidths: preferences.columnWidths,
    toggleColumn,
    setColumnWidth,
    restoreDefaultLayout,
  };
}
