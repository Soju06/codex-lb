import type { ApiKey } from "@/features/api-keys/schemas";

export type ApiViewMode = "detail" | "list";
export const API_LIST_COLUMNS =
  "lg:grid-cols-[minmax(0,1.4fr)_minmax(0,.6fr)_minmax(0,.6fr)_minmax(0,.9fr)_minmax(0,.9fr)_minmax(0,1.8fr)]";
export const API_SORT_COLUMNS = [
  "name",
  "status",
  "requests",
  "expiry",
  "lastUsed",
] as const;
export type ApiSortColumn = (typeof API_SORT_COLUMNS)[number];
export type ApiSortMode = "default" | `${ApiSortColumn}_${"asc" | "desc"}`;

export function apiKeyStatus(key: ApiKey): "active" | "disabled" | "expired" {
  if (!key.isActive) return "disabled";
  return key.expiresAt && Date.parse(key.expiresAt) <= Date.now()
    ? "expired"
    : "active";
}

export function sortApiKeys(keys: ApiKey[], mode: ApiSortMode): ApiKey[] {
  if (mode === "default") return keys;
  const direction = mode.endsWith("_desc") ? -1 : 1;
  const column = mode.split("_")[0] as ApiSortColumn;
  const value = (key: ApiKey): number => {
    if (column === "status")
      return { active: 0, disabled: 1, expired: 2 }[apiKeyStatus(key)];
    if (column === "requests") return key.usageSummary?.requestCount ?? NaN;
    const date = column === "expiry" ? key.expiresAt : key.lastUsedAt;
    return date ? Date.parse(date) : NaN;
  };
  return [...keys].sort((left, right) => {
    if (column === "name")
      return direction * left.name.localeCompare(right.name);
    const a = value(left);
    const b = value(right);
    if (!Number.isFinite(a)) return Number.isFinite(b) ? 1 : 0;
    if (!Number.isFinite(b)) return -1;
    return direction * (a - b);
  });
}
