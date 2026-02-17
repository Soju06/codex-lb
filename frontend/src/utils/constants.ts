export const STATUS_LABELS = {
  active: "Active",
  paused: "Paused",
  limited: "Rate limited",
  exceeded: "Quota exceeded",
  deactivated: "Deactivated",
} as const;

export const ERROR_LABELS = {
  rate_limit: "rate limit",
  quota: "quota",
  timeout: "timeout",
  upstream: "upstream",
  rate_limit_exceeded: "rate limit",
  usage_limit_reached: "quota",
  insufficient_quota: "quota",
  usage_not_included: "quota",
  quota_exceeded: "quota",
  upstream_error: "upstream",
} as const;

export const ROUTING_LABELS = {
  usage_weighted: "usage weighted",
  round_robin: "round robin",
  sticky: "sticky",
} as const;

export const KNOWN_PLAN_TYPES = new Set([
  "free",
  "plus",
  "pro",
  "team",
  "business",
  "enterprise",
  "edu",
]);

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

export const DONUT_COLORS = DONUT_COLORS_LIGHT;

export const MESSAGE_TONE_META = {
  success: {
    label: "Success",
    className: "active",
    defaultTitle: "Import complete",
  },
  error: {
    label: "Error",
    className: "deactivated",
    defaultTitle: "Import failed",
  },
  warning: {
    label: "Warning",
    className: "limited",
    defaultTitle: "Attention",
  },
  info: {
    label: "Info",
    className: "limited",
    defaultTitle: "Message",
  },
  question: {
    label: "Question",
    className: "limited",
    defaultTitle: "Confirm",
  },
} as const;

export const REQUEST_STATUS_LABELS: Record<string, string> = {
  ok: "OK",
  rate_limit: "Rate limit",
  quota: "Quota",
  error: "Error",
};

export const RESET_ERROR_LABEL = "--";
