import { create } from "zustand";

const THEME_STORAGE_KEY = "codex-lb-theme";

export type Theme = "light" | "dark";

type ThemeState = {
  theme: Theme;
  initialized: boolean;
  initializeTheme: () => void;
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
};

function applyThemeToDocument(theme: Theme): void {
  if (typeof document === "undefined") {
    return;
  }
  document.documentElement.classList.toggle("dark", theme === "dark");
}

function readStoredTheme(): Theme | null {
  if (typeof window === "undefined") {
    return null;
  }
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (stored === "light" || stored === "dark") {
    return stored;
  }
  return null;
}

function resolveInitialTheme(): Theme {
  const stored = readStoredTheme();
  if (stored) {
    return stored;
  }
  if (typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
}

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: "light",
  initialized: false,
  initializeTheme: () => {
    const next = resolveInitialTheme();
    applyThemeToDocument(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    }
    set({ theme: next, initialized: true });
  },
  setTheme: (theme) => {
    applyThemeToDocument(theme);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    }
    set({ theme, initialized: true });
  },
  toggleTheme: () => {
    const nextTheme: Theme = get().theme === "dark" ? "light" : "dark";
    get().setTheme(nextTheme);
  },
}));
