import { create } from "zustand";

const TIME_FORMAT_STORAGE_KEY = "codex-lb-time-format";

export type TimeFormatPreference = "12h" | "24h";

type TimeFormatState = {
  timeFormat: TimeFormatPreference;
  setTimeFormat: (preference: TimeFormatPreference) => void;
};

function readStoredPreference(): TimeFormatPreference {
  if (typeof window === "undefined") {
    return "12h";
  }

  try {
    const stored = window.localStorage.getItem(TIME_FORMAT_STORAGE_KEY);
    return stored === "24h" ? "24h" : "12h";
  } catch {
    return "12h";
  }
}

function persistPreference(preference: TimeFormatPreference): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(TIME_FORMAT_STORAGE_KEY, preference);
  } catch {
    /* Storage blocked - silently ignore. */
  }
}

export function getTimeFormatPreference(): TimeFormatPreference {
  return useTimeFormatStore.getState().timeFormat;
}

export const useTimeFormatStore = create<TimeFormatState>((set) => ({
  timeFormat: readStoredPreference(),
  setTimeFormat: (preference) => {
    persistPreference(preference);
    set({ timeFormat: preference });
  },
}));
