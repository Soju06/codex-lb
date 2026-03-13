import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useThemeStore } from "@/hooks/use-theme";

const THEME_STORAGE_KEY = "codex-lb-theme";
const localStorageMock = createLocalStorageMock();

function createLocalStorageMock(): Storage {
  const store = new Map<string, string>();

  return {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.get(key) ?? null;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key: string) {
      store.delete(key);
    },
    setItem(key: string, value: string) {
      store.set(key, value);
    },
  };
}

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
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: localStorageMock,
    });
    window.localStorage.clear();
    document.documentElement.classList.remove("dark");
    useThemeStore.setState({ preference: "auto", theme: "light", initialized: false });
    mockMatchMedia(false);
  });

  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("persists preference in localStorage", () => {
    useThemeStore.getState().setTheme("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");

    useThemeStore.getState().setTheme("auto");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("auto");

    useThemeStore.getState().setTheme("light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });

  it("resolves auto to system preference (dark)", () => {
    mockMatchMedia(true);
    useThemeStore.getState().setTheme("auto");

    expect(useThemeStore.getState().preference).toBe("auto");
    expect(useThemeStore.getState().theme).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("resolves auto to system preference (light)", () => {
    mockMatchMedia(false);
    useThemeStore.getState().setTheme("auto");

    expect(useThemeStore.getState().preference).toBe("auto");
    expect(useThemeStore.getState().theme).toBe("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("initializes from saved preference", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    useThemeStore.getState().initializeTheme();

    expect(useThemeStore.getState().preference).toBe("dark");
    expect(useThemeStore.getState().theme).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("initializes auto preference from localStorage", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "auto");
    mockMatchMedia(true);
    useThemeStore.getState().initializeTheme();

    expect(useThemeStore.getState().preference).toBe("auto");
    expect(useThemeStore.getState().theme).toBe("dark");
  });

  it("defaults to auto when no stored preference", () => {
    mockMatchMedia(false);
    useThemeStore.getState().initializeTheme();

    expect(useThemeStore.getState().preference).toBe("auto");
    expect(useThemeStore.getState().theme).toBe("light");
  });

  it("syncs html dark class", () => {
    useThemeStore.getState().setTheme("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);

    useThemeStore.getState().setTheme("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });
});
