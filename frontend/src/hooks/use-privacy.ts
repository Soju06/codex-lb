import { create } from "zustand";

const PRIVACY_STORAGE_KEY = "codex-lb-privacy";

type PrivacyState = {
  /** Whether emails are blurred across the dashboard. */
  blurred: boolean;
  /** Toggle email blur on/off and persist to localStorage. */
  toggle: () => void;
};

function readStored(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(PRIVACY_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function persist(value: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(PRIVACY_STORAGE_KEY, value ? "1" : "0");
  } catch {
    /* Storage blocked — silently ignore. */
  }
}

export const usePrivacyStore = create<PrivacyState>((set, get) => ({
  blurred: readStored(),
  toggle: () => {
    const next = !get().blurred;
    persist(next);
    set({ blurred: next });
  },
}));
