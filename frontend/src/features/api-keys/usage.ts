import type { ApiKey } from "./schemas";

export function hasApiKeyUsage(key: ApiKey): boolean {
  return key.lastUsedAt !== null || (key.usageSummary?.requestCount ?? 0) > 0;
}
