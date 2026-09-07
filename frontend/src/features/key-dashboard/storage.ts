export const KEY_DASHBOARD_API_KEY_STORAGE_KEY = "codex-lb-key-dashboard-api-key";

export function getRememberedApiKey(): string | null {
  try {
    const value = window.localStorage.getItem(KEY_DASHBOARD_API_KEY_STORAGE_KEY)?.trim();
    return value || null;
  } catch {
    return null;
  }
}

export function rememberApiKey(apiKey: string): void {
  try {
    window.localStorage.setItem(KEY_DASHBOARD_API_KEY_STORAGE_KEY, apiKey);
  } catch {
    // The dashboard still works in memory when browser storage is unavailable.
  }
}

export function forgetRememberedApiKey(): void {
  try {
    window.localStorage.removeItem(KEY_DASHBOARD_API_KEY_STORAGE_KEY);
  } catch {
    // Nothing else is required when browser storage is unavailable.
  }
}
