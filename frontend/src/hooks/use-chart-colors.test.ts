import { describe, expect, it, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";

import { useChartColors } from "@/hooks/use-chart-colors";

describe("useChartColors", () => {
  beforeEach(() => {
    // Set CSS variables on document root so getComputedStyle can find them
    const root = document.documentElement;
    root.style.setProperty("--chart-1", "#3b82f6");
    root.style.setProperty("--chart-2", "#8b5cf6");
    root.style.setProperty("--chart-3", "#10b981");
    root.style.setProperty("--chart-4", "#f59e0b");
    root.style.setProperty("--chart-5", "#ec4899");
  });

  it("returns an array of 5 colors", () => {
    const { result } = renderHook(() => useChartColors());
    expect(result.current).toHaveLength(5);
    result.current.forEach((color) => {
      expect(typeof color).toBe("string");
      expect(color.length).toBeGreaterThan(0);
    });
  });

  it("returns fallback colors when CSS variables are empty", () => {
    const root = document.documentElement;
    for (let i = 1; i <= 5; i++) {
      root.style.removeProperty(`--chart-${i}`);
    }

    const { result } = renderHook(() => useChartColors());
    expect(result.current).toHaveLength(5);
    // Should still return valid color strings (fallbacks)
    result.current.forEach((color) => {
      expect(color).toMatch(/^#[0-9a-f]{6}$/i);
    });
  });
});
