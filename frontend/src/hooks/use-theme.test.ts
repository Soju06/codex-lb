import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useThemeStore } from "@/hooks/use-theme";

const THEME_STORAGE_KEY = "codex-lb-theme";

function mockMatchMedia(matches = false): void {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches,
      media: "(prefers-color-scheme: dark)",
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

describe("useThemeStore", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.classList.remove("dark");
    useThemeStore.setState({ theme: "light", initialized: false });
    mockMatchMedia(false);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("toggles theme and syncs html class", () => {
    const store = useThemeStore.getState();
    store.initializeTheme();
    expect(useThemeStore.getState().theme).toBe("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    useThemeStore.getState().toggleTheme();
    expect(useThemeStore.getState().theme).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("persists theme in localStorage", () => {
    useThemeStore.getState().setTheme("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");

    useThemeStore.getState().setTheme("light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("initializes from saved theme", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    useThemeStore.getState().initializeTheme();

    expect(useThemeStore.getState().theme).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});
