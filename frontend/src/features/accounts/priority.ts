export const ACCOUNT_PRIORITIES = ["gold", "silver", "bronze"] as const;

export type AccountPriority = (typeof ACCOUNT_PRIORITIES)[number];

export const ACCOUNT_PRIORITY_LABELS: Record<AccountPriority, string> = {
  gold: "Gold",
  silver: "Silver",
  bronze: "Bronze",
};

export const ACCOUNT_PRIORITY_ORDER: Record<AccountPriority, number> = {
  gold: 0,
  silver: 1,
  bronze: 2,
};

export function normalizeAccountPriority(value: string | null | undefined): AccountPriority {
  const normalized = (value ?? "").trim().toLowerCase();
  if (normalized === "gold") return "gold";
  if (normalized === "bronze") return "bronze";
  return "silver";
}

export function accountPriorityRank(value: string | null | undefined): number {
  return ACCOUNT_PRIORITY_ORDER[normalizeAccountPriority(value)];
}

export function formatAccountPriorityLabel(value: string | null | undefined): string {
  return ACCOUNT_PRIORITY_LABELS[normalizeAccountPriority(value)];
}

export const ACCOUNT_PRIORITY_OPTIONS = ACCOUNT_PRIORITIES.map((priority) => ({
  value: priority,
  label: ACCOUNT_PRIORITY_LABELS[priority],
}));
