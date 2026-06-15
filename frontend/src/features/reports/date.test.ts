import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  daysAgoLocalISO,
  formatReportBucketDate,
  getBrowserReportsTimeZone,
  localDateISO,
} from "./date";

const REPORTS_TIMEZONE_STORAGE_KEY = "codex-lb-reports-timezone";
const originalLocalStorageDescriptor = Object.getOwnPropertyDescriptor(window, "localStorage");

describe("reports date helpers", () => {
  beforeEach(() => {
    if (originalLocalStorageDescriptor) {
      Object.defineProperty(window, "localStorage", originalLocalStorageDescriptor);
    }
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    if (originalLocalStorageDescriptor) {
      Object.defineProperty(window, "localStorage", originalLocalStorageDescriptor);
      window.localStorage.clear();
    }
  });

  function createBlockedLocalStorage(): Storage {
    return {
      get length() {
        return 0;
      },
      clear() {},
      getItem() {
        return null;
      },
      key() {
        return null;
      },
      removeItem() {},
      setItem() {
        throw new DOMException("blocked", "SecurityError");
      },
    };
  }

  function createReadBlockedLocalStorage(): Storage {
    return {
      get length() {
        return 0;
      },
      clear() {},
      getItem() {
        throw new DOMException("blocked", "SecurityError");
      },
      key() {
        return null;
      },
      removeItem() {},
      setItem() {},
    };
  }

  it("formats a local calendar date without shifting it to the UTC day", () => {
    const localLateEvening = new Date("2026-06-02T02:30:00.000Z");
    vi.spyOn(localLateEvening, "getTimezoneOffset").mockReturnValue(300);

    expect(localDateISO(localLateEvening)).toBe("2026-06-01");
  });

  it("shifts the local calendar date back by the requested number of days", () => {
    const localNoon = new Date(2026, 5, 2, 12, 0, 0);

    expect(daysAgoLocalISO(1, localNoon)).toBe("2026-06-01");
  });

  it("formats report bucket strings without parsing them as UTC instants", () => {
    expect(formatReportBucketDate("2026-06-01")).toBe("2026-06-01");
  });

  it("returns the live browser timezone and refreshes the cached value", () => {
    window.localStorage.setItem(REPORTS_TIMEZONE_STORAGE_KEY, "UTC");
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      locale: "en-US",
      calendar: "gregory",
      numberingSystem: "latn",
      timeZone: "America/Los_Angeles",
    });

    expect(getBrowserReportsTimeZone()).toBe("America/Los_Angeles");
    expect(window.localStorage.getItem(REPORTS_TIMEZONE_STORAGE_KEY)).toBe(
      "America/Los_Angeles",
    );
  });

  it("returns the live browser timezone when localStorage writes are blocked", () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      locale: "en-US",
      calendar: "gregory",
      numberingSystem: "latn",
      timeZone: "America/Los_Angeles",
    });
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: createBlockedLocalStorage(),
    });

    expect(getBrowserReportsTimeZone()).toBe("America/Los_Angeles");
    expect(window.localStorage.getItem(REPORTS_TIMEZONE_STORAGE_KEY)).toBeNull();
  });

  it("validates the live browser timezone when supportedValuesOf is unavailable", () => {
    const originalSupportedValuesOf = Object.getOwnPropertyDescriptor(Intl, "supportedValuesOf");
    Object.defineProperty(Intl, "supportedValuesOf", {
      configurable: true,
      value: undefined,
    });
    try {
      vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
        locale: "en-US",
        calendar: "gregory",
        numberingSystem: "latn",
        timeZone: "Europe/Paris",
      });

      expect(getBrowserReportsTimeZone()).toBe("Europe/Paris");
      expect(window.localStorage.getItem(REPORTS_TIMEZONE_STORAGE_KEY)).toBe("Europe/Paris");
    } finally {
      if (originalSupportedValuesOf) {
        Object.defineProperty(Intl, "supportedValuesOf", originalSupportedValuesOf);
      } else {
        delete (Intl as { supportedValuesOf?: unknown }).supportedValuesOf;
      }
    }
  });

  it("reuses the cached timezone when the live browser timezone is invalid", () => {
    window.localStorage.setItem(REPORTS_TIMEZONE_STORAGE_KEY, "Europe/Paris");
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      locale: "en-US",
      calendar: "gregory",
      numberingSystem: "latn",
      timeZone: "Mars/Olympus",
    });

    expect(getBrowserReportsTimeZone()).toBe("Europe/Paris");
    expect(window.localStorage.getItem(REPORTS_TIMEZONE_STORAGE_KEY)).toBe("Europe/Paris");
  });

  it("reuses the cached timezone when live browser detection is unavailable", () => {
    window.localStorage.setItem(REPORTS_TIMEZONE_STORAGE_KEY, "Europe/Paris");
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      locale: "en-US",
      calendar: "gregory",
      numberingSystem: "latn",
      timeZone: undefined as unknown as string,
    });

    expect(getBrowserReportsTimeZone()).toBe("Europe/Paris");
    expect(window.localStorage.getItem(REPORTS_TIMEZONE_STORAGE_KEY)).toBe("Europe/Paris");
  });

  it("returns undefined when neither the live nor cached timezone is valid", () => {
    window.localStorage.setItem(REPORTS_TIMEZONE_STORAGE_KEY, "Moon/BaseAlpha");
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      locale: "en-US",
      calendar: "gregory",
      numberingSystem: "latn",
      timeZone: "Mars/Olympus",
    });

    expect(getBrowserReportsTimeZone()).toBeUndefined();
  });

  it("returns undefined when the live timezone is invalid and no cached timezone exists", () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      locale: "en-US",
      calendar: "gregory",
      numberingSystem: "latn",
      timeZone: "Mars/Olympus",
    });

    expect(getBrowserReportsTimeZone()).toBeUndefined();
    expect(window.localStorage.getItem(REPORTS_TIMEZONE_STORAGE_KEY)).toBeNull();
  });

  it.each(["Mars/Olympus", undefined])(
    "returns undefined when cached timezone reads are blocked and live detection is %s",
    (timeZone) => {
      vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
        locale: "en-US",
        calendar: "gregory",
        numberingSystem: "latn",
        timeZone: timeZone as unknown as string,
      });
      Object.defineProperty(window, "localStorage", {
        configurable: true,
        value: createReadBlockedLocalStorage(),
      });

      expect(getBrowserReportsTimeZone()).toBeUndefined();
    },
  );
});
