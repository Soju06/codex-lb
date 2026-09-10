export const DONUT_COLORS_LIGHT = [
  "#3b82f6",
  "#8b5cf6",
  "#10b981",
  "#f59e0b",
  "#ec4899",
  "#06b6d4",
] as const;

export const DONUT_COLORS_DARK = [
  "#2563eb",
  "#7c3aed",
  "#059669",
  "#d97706",
  "#db2777",
  "#0891b2",
] as const;

export const REQUEST_STATUS_LABELS: Record<string, string> = {
  ok: "OK",
  cancelled: "Cancelled",
  rate_limit: "Rate limit",
  quota: "Quota",
  error: "Error",
};

export const RESET_ERROR_LABEL = "--";
