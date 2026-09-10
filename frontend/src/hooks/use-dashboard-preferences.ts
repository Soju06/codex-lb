import { create } from "zustand";

import type { AccountListSort, AccountListSortKey } from "@/features/dashboard/components/account-list";

const ACCOUNT_BURNRATE_STORAGE_KEY = "codex-lb-account-burnrate-enabled";
const ACCOUNT_VIEW_MODE_STORAGE_KEY = "codex-lb-dashboard-account-view-mode";
const ACCOUNT_LIST_SORT_STORAGE_KEY = "codex-lb-dashboard-account-list-sort";
const DASHBOARD_REFRESH_STORAGE_KEY = "codex-lb-dashboard-refresh-seconds";

export type DashboardRefreshSeconds = 5 | 15 | 30 | 60;

export type DashboardAccountViewMode = "cards" | "list";

type DashboardPreferencesState = {
  accountBurnrateEnabled: boolean;
  accountViewMode: DashboardAccountViewMode;
  accountListSort: AccountListSort;
  refreshSeconds: DashboardRefreshSeconds;
  initialized: boolean;
  initializePreferences: () => void;
  setAccountBurnrateEnabled: (enabled: boolean) => void;
  setAccountViewMode: (mode: DashboardAccountViewMode) => void;
  setAccountListSort: (sort: AccountListSort) => void;
  setRefreshSeconds: (seconds: DashboardRefreshSeconds) => void;
};

const ACCOUNT_LIST_SORT_KEYS: AccountListSortKey[] = [
  "account",
  "status",
  "plan",
  "quota",
  "subscriptionCredits",
  "purchasedCredits",
  "warmup",
];

function isAccountListSortKey(value: unknown): value is AccountListSortKey {
  return typeof value === "string" && ACCOUNT_LIST_SORT_KEYS.includes(value as AccountListSortKey);
}

function readStoredAccountBurnrateEnabled(): boolean | null {
  if (typeof window === "undefined") {
    return null;
  }
  const stored = window.localStorage.getItem(ACCOUNT_BURNRATE_STORAGE_KEY);
  if (stored === "true") {
    return true;
  }
  if (stored === "false") {
    return false;
  }
  return null;
}

function readStoredAccountViewMode(): DashboardAccountViewMode | null {
  if (typeof window === "undefined") {
    return null;
  }
  const stored = window.localStorage.getItem(ACCOUNT_VIEW_MODE_STORAGE_KEY);
  return stored === "cards" || stored === "list" ? stored : null;
}

function readStoredAccountListSort(): AccountListSort {
  if (typeof window === "undefined") {
    return null;
  }
  const stored = window.localStorage.getItem(ACCOUNT_LIST_SORT_STORAGE_KEY);
  if (!stored) {
    return null;
  }
  try {
    const parsed = JSON.parse(stored) as { key?: unknown; direction?: unknown };
    if (parsed.key === "credits") {
      parsed.key = "purchasedCredits";
    }
    if (
      isAccountListSortKey(parsed.key) &&
      (parsed.direction === "asc" || parsed.direction === "desc")
    ) {
      return { key: parsed.key, direction: parsed.direction };
    }
  } catch {
    return null;
  }
  return null;
}

function readStoredRefreshSeconds(): DashboardRefreshSeconds | null {
  if (typeof window === "undefined") {
    return null;
  }
  const numeric = Number(window.localStorage.getItem(DASHBOARD_REFRESH_STORAGE_KEY));
  return numeric === 5 || numeric === 15 || numeric === 30 || numeric === 60
    ? numeric
    : null;
}

function persistAccountBurnrateEnabled(enabled: boolean): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(ACCOUNT_BURNRATE_STORAGE_KEY, String(enabled));
}

function persistAccountViewMode(mode: DashboardAccountViewMode): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(ACCOUNT_VIEW_MODE_STORAGE_KEY, mode);
}

function persistAccountListSort(sort: AccountListSort): void {
  if (typeof window === "undefined") {
    return;
  }
  if (sort === null) {
    window.localStorage.removeItem(ACCOUNT_LIST_SORT_STORAGE_KEY);
    return;
  }
  window.localStorage.setItem(ACCOUNT_LIST_SORT_STORAGE_KEY, JSON.stringify(sort));
}

function persistRefreshSeconds(seconds: DashboardRefreshSeconds): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(DASHBOARD_REFRESH_STORAGE_KEY, String(seconds));
}

export const useDashboardPreferencesStore = create<DashboardPreferencesState>((set) => ({
  accountBurnrateEnabled: true,
  accountViewMode: "cards",
  accountListSort: null,
  refreshSeconds: 15,
  initialized: false,
  initializePreferences: () => {
    const accountBurnrateEnabled = readStoredAccountBurnrateEnabled() ?? true;
    const accountViewMode = readStoredAccountViewMode() ?? "cards";
    const accountListSort = readStoredAccountListSort();
    const refreshSeconds = readStoredRefreshSeconds() ?? 15;
    persistAccountBurnrateEnabled(accountBurnrateEnabled);
    persistAccountViewMode(accountViewMode);
    persistAccountListSort(accountListSort);
    persistRefreshSeconds(refreshSeconds);
    set({ accountBurnrateEnabled, accountViewMode, accountListSort, refreshSeconds, initialized: true });
  },
  setAccountBurnrateEnabled: (enabled) => {
    persistAccountBurnrateEnabled(enabled);
    set({ accountBurnrateEnabled: enabled, initialized: true });
  },
  setAccountViewMode: (mode) => {
    persistAccountViewMode(mode);
    set({ accountViewMode: mode, initialized: true });
  },
  setAccountListSort: (sort) => {
    persistAccountListSort(sort);
    set({ accountListSort: sort, initialized: true });
  },
  setRefreshSeconds: (seconds) => {
    persistRefreshSeconds(seconds);
    set({ refreshSeconds: seconds, initialized: true });
  },
}));
