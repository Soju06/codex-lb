import { afterEach, describe, expect, it } from "vitest";

import { ensureLocalStorageShim } from "@/test/local-storage-shim";

const originalLocalStorageDescriptor = Object.getOwnPropertyDescriptor(window, "localStorage");

function restoreLocalStorage(): void {
  if (originalLocalStorageDescriptor) {
    Object.defineProperty(window, "localStorage", originalLocalStorageDescriptor);
  }
}

describe("ensureLocalStorageShim", () => {
  afterEach(() => {
    restoreLocalStorage();
  });

  it("installs the shim when localStorage is undefined", () => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: undefined,
    });

    expect(() => ensureLocalStorageShim()).not.toThrow();
    expect(typeof window.localStorage.clear).toBe("function");
  });

  it("installs the shim when reading localStorage throws", () => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new DOMException("sandboxed", "SecurityError");
      },
    });

    expect(() => ensureLocalStorageShim()).not.toThrow();
    expect(typeof window.localStorage.clear).toBe("function");
  });
});
