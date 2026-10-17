import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useSmoothPercent } from "@/hooks/use-smooth-percent";

describe("useSmoothPercent", () => {
  let now = 0;
  let nextFrameId = 1;
  const frames = new Map<number, FrameRequestCallback>();

  beforeEach(() => {
    vi.spyOn(performance, "now").mockImplementation(() => now);
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      const id = nextFrameId++;
      frames.set(id, callback);
      return id;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  });

  afterEach(() => {
    frames.clear();
    now = 0;
    nextFrameId = 1;
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function runFrame(at: number) {
    now = at;
    const callbacks = [...frames.values()];
    frames.clear();
    callbacks.forEach((callback) => callback(at));
  }

  it("holds the last valid value while a refresh is temporarily unknown", () => {
    const { result, rerender } = renderHook(
      ({ value }) => useSmoothPercent(value),
      { initialProps: { value: 82 as number | null } },
    );

    rerender({ value: null });
    expect(result.current.percent).toBe(82);
    expect(result.current.everKnown).toBe(true);
  });

  it("treats non-finite refresh values as unknown", () => {
    const { result, rerender } = renderHook(
      ({ value }) => useSmoothPercent(value),
      { initialProps: { value: 64 as number | null } },
    );

    rerender({ value: Number.NaN });
    expect(result.current.percent).toBe(64);
  });

  it("applies fresh values without animation when reduced motion is preferred", () => {
    const originalMatchMedia = window.matchMedia;
    window.matchMedia = (query: string) =>
      ({
        matches: query === "(prefers-reduced-motion: reduce)",
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }) as unknown as MediaQueryList;

    try {
      const { result, rerender } = renderHook(
        ({ value }) => useSmoothPercent(value),
        { initialProps: { value: 80 as number | null } },
      );

      rerender({ value: 20 });
      expect(result.current.percent).toBe(20);
      expect(frames.size).toBe(0);
    } finally {
      window.matchMedia = originalMatchMedia;
    }
  });

  it("eases from the displayed value to the next value", () => {
    const { result, rerender } = renderHook(
      ({ value }) => useSmoothPercent(value),
      { initialProps: { value: 80 as number | null } },
    );

    rerender({ value: 20 });
    act(() => runFrame(500));
    expect(result.current.percent).toBeLessThan(80);
    expect(result.current.percent).toBeGreaterThan(20);

    act(() => runFrame(1_000));
    expect(result.current.percent).toBe(20);
  });
});
