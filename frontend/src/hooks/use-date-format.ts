import { create } from "zustand";

export const DATE_DISPLAY_FORMAT_STORAGE_KEY = "codex-lb-date-display-format";

export type DateDisplayFormat = "default" | "iso8601";

type DateDisplayFormatState = {
  dateDisplayFormat: DateDisplayFormat;
  setDateDisplayFormat: (format: DateDisplayFormat) => void;
};

function readStoredFormat(): DateDisplayFormat {
  if (typeof window === "undefined") {
    return "default";
  }

  try {
    const stored = window.localStorage.getItem(DATE_DISPLAY_FORMAT_STORAGE_KEY);
    return stored === "iso8601" ? "iso8601" : "default";
  } catch {
    return "default";
  }
}

function persistFormat(format: DateDisplayFormat): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(DATE_DISPLAY_FORMAT_STORAGE_KEY, format);
  } catch {
    /* Storage blocked - silently ignore. */
  }
}

export function getDateDisplayFormat(): DateDisplayFormat {
  return useDateDisplayFormatStore.getState().dateDisplayFormat;
}

export const useDateDisplayFormatStore = create<DateDisplayFormatState>((set) => ({
  dateDisplayFormat: readStoredFormat(),
  setDateDisplayFormat: (format) => {
    persistFormat(format);
    set({ dateDisplayFormat: format });
  },
}));
