import { create } from "zustand";

const QUOTA_DISPLAY_STORAGE_KEY = "codex-lb-account-quota-display";

export type AccountQuotaDisplayPreference = "5h" | "weekly" | "both";

type AccountQuotaDisplayState = {
  quotaDisplay: AccountQuotaDisplayPreference;
  setQuotaDisplay: (preference: AccountQuotaDisplayPreference) => void;
};

function readStoredPreference(): AccountQuotaDisplayPreference {
  if (typeof window === "undefined") {
    return "both";
  }

  try {
    const stored = window.localStorage.getItem(QUOTA_DISPLAY_STORAGE_KEY);
    return stored === "5h" || stored === "weekly" || stored === "both" ? stored : "both";
  } catch {
    return "both";
  }
}

function persistPreference(preference: AccountQuotaDisplayPreference): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(QUOTA_DISPLAY_STORAGE_KEY, preference);
  } catch {
    /* Storage blocked - silently ignore. */
  }
}

export function getAccountQuotaDisplayPreference(): AccountQuotaDisplayPreference {
  return useAccountQuotaDisplayStore.getState().quotaDisplay;
}

export const useAccountQuotaDisplayStore = create<AccountQuotaDisplayState>((set) => ({
  quotaDisplay: readStoredPreference(),
  setQuotaDisplay: (preference) => {
    persistPreference(preference);
    set({ quotaDisplay: preference });
  },
}));
