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

describe("useAccountQuotaDisplayStore", () => {
  beforeEach(() => {
    installLocalStorageMock();
    vi.resetModules();
  });

  it("defaults to both", async () => {
    const { getAccountQuotaDisplayPreference } = await import("@/hooks/use-account-quota-display");

    expect(getAccountQuotaDisplayPreference()).toBe("both");
  });

  it("persists updates to localStorage", async () => {
    const { getAccountQuotaDisplayPreference, useAccountQuotaDisplayStore } = await import(
      "@/hooks/use-account-quota-display"
    );

    useAccountQuotaDisplayStore.getState().setQuotaDisplay("weekly");

    expect(getAccountQuotaDisplayPreference()).toBe("weekly");
    expect(window.localStorage.getItem("codex-lb-account-quota-display")).toBe("weekly");
  });
});
