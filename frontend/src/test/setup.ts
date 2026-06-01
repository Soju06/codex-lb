import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";

import { ensureLocalStorageShim } from "@/test/local-storage-shim";
import { resetMockState } from "@/test/mocks/handlers";
import { server, startMockServer } from "@/test/mocks/server";

if (typeof window !== "undefined" && typeof window.matchMedia !== "function") {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

if (typeof document !== "undefined" && typeof document.elementFromPoint !== "function") {
  document.elementFromPoint = () => null;
}

ensureLocalStorageShim();

if (typeof Element !== "undefined") {
  const proto = Element.prototype as unknown as Record<string, unknown>;
  if (typeof proto.hasPointerCapture !== "function") {
    Object.defineProperty(Element.prototype, "hasPointerCapture", {
      configurable: true,
      value: () => false,
    });
  }
  if (typeof proto.setPointerCapture !== "function") {
    Object.defineProperty(Element.prototype, "setPointerCapture", {
      configurable: true,
      value: () => {},
    });
  }
  if (typeof proto.releasePointerCapture !== "function") {
    Object.defineProperty(Element.prototype, "releasePointerCapture", {
      configurable: true,
      value: () => {},
    });
  }
  if (typeof proto.scrollIntoView !== "function") {
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: () => {},
    });
  }
}

if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverMock {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverMock;
}

beforeAll(() => {
  configure({ asyncUtilTimeout: 10_000 });
  startMockServer();
});

afterEach(() => {
  if (typeof window !== "undefined") {
    window.history.replaceState({}, "", "/");
  }
  resetMockState();
  server.resetHandlers();
  cleanup();
});

afterAll(() => {
  server.close();
});
