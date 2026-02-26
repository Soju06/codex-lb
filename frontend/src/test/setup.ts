import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";

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
