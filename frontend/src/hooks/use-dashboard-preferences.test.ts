import { beforeEach, describe, expect, it, vi } from "vitest";

function installLocalStorageMock() {
  const storage = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => {
        storage.set(key, value);
      },
      removeItem: (key: string) => {
        storage.delete(key);
      },
      clear: () => {
        storage.clear();
      },
    },
  });
}

describe("useDashboardPreferencesStore", () => {
  beforeEach(() => {
    installLocalStorageMock();
    vi.resetModules();
  });

  it("defaults account view mode to cards", async () => {
    const { useDashboardPreferencesStore } = await import("@/hooks/use-dashboard-preferences");

    useDashboardPreferencesStore.getState().initializePreferences();

    expect(useDashboardPreferencesStore.getState().accountViewMode).toBe("cards");
    expect(useDashboardPreferencesStore.getState().accountListSort).toBeNull();
    expect(useDashboardPreferencesStore.getState().refreshSeconds).toBe(15);
    expect(window.localStorage.getItem("codex-lb-dashboard-account-view-mode")).toBe("cards");
    expect(window.localStorage.getItem("codex-lb-dashboard-account-list-sort")).toBeNull();
    expect(window.localStorage.getItem("codex-lb-dashboard-refresh-seconds")).toBe("15");
  });

  it("persists a valid dashboard refresh cadence", async () => {
    const { useDashboardPreferencesStore } = await import("@/hooks/use-dashboard-preferences");

    useDashboardPreferencesStore.getState().setRefreshSeconds(5);

    expect(useDashboardPreferencesStore.getState().refreshSeconds).toBe(5);
    expect(window.localStorage.getItem("codex-lb-dashboard-refresh-seconds")).toBe("5");
  });

  it("restores a stored refresh cadence on initialize", async () => {
    window.localStorage.setItem("codex-lb-dashboard-refresh-seconds", "30");
    const { useDashboardPreferencesStore } = await import("@/hooks/use-dashboard-preferences");

    useDashboardPreferencesStore.getState().initializePreferences();

    expect(useDashboardPreferencesStore.getState().refreshSeconds).toBe(30);
    expect(window.localStorage.getItem("codex-lb-dashboard-refresh-seconds")).toBe("30");
  });

  it("ignores an invalid stored refresh cadence", async () => {
    window.localStorage.setItem("codex-lb-dashboard-refresh-seconds", "7");
    const { useDashboardPreferencesStore } = await import("@/hooks/use-dashboard-preferences");

    useDashboardPreferencesStore.getState().initializePreferences();

    expect(useDashboardPreferencesStore.getState().refreshSeconds).toBe(15);
    expect(window.localStorage.getItem("codex-lb-dashboard-refresh-seconds")).toBe("15");
  });

  it("persists account view mode updates", async () => {
    const { useDashboardPreferencesStore } = await import("@/hooks/use-dashboard-preferences");

    useDashboardPreferencesStore.getState().setAccountViewMode("list");

    expect(useDashboardPreferencesStore.getState().accountViewMode).toBe("list");
    expect(window.localStorage.getItem("codex-lb-dashboard-account-view-mode")).toBe("list");
  });

  it("persists account list sort updates", async () => {
    const { useDashboardPreferencesStore } = await import("@/hooks/use-dashboard-preferences");

    useDashboardPreferencesStore.getState().setAccountListSort({ key: "quota", direction: "asc" });

    expect(useDashboardPreferencesStore.getState().accountListSort).toEqual({ key: "quota", direction: "asc" });
    expect(window.localStorage.getItem("codex-lb-dashboard-account-list-sort")).toBe(
      JSON.stringify({ key: "quota", direction: "asc" }),
    );
  });

  it("migrates the legacy credits sort to purchased credits", async () => {
    window.localStorage.setItem(
      "codex-lb-dashboard-account-list-sort",
      JSON.stringify({ key: "credits", direction: "desc" }),
    );
    const { useDashboardPreferencesStore } = await import("@/hooks/use-dashboard-preferences");

    useDashboardPreferencesStore.getState().initializePreferences();

    expect(useDashboardPreferencesStore.getState().accountListSort).toEqual({ key: "purchasedCredits", direction: "desc" });
    expect(window.localStorage.getItem("codex-lb-dashboard-account-list-sort")).toBe(
      JSON.stringify({ key: "purchasedCredits", direction: "desc" }),
    );
  });

  it("ignores invalid stored account list sort", async () => {
    window.localStorage.setItem(
      "codex-lb-dashboard-account-list-sort",
      JSON.stringify({ key: "invalid", direction: "desc" }),
    );
    const { useDashboardPreferencesStore } = await import("@/hooks/use-dashboard-preferences");

    useDashboardPreferencesStore.getState().initializePreferences();

    expect(useDashboardPreferencesStore.getState().accountListSort).toBeNull();
    expect(window.localStorage.getItem("codex-lb-dashboard-account-list-sort")).toBeNull();
  });
});
