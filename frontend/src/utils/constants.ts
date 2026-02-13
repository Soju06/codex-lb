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

export const DONUT_COLORS = [
  "#7bb661",
  "#d9a441",
  "#4b6ea8",
  "#c35d5d",
  "#8d6bd6",
  "#4aa0a8",
] as const;

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

export const RESET_ERROR_LABEL = "--";
