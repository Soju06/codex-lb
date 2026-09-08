import { describe, expect, it } from "vitest";

import { DONUT_COLORS_DARK, DONUT_COLORS_LIGHT } from "@/utils/constants";

describe("DONUT_COLORS_LIGHT / DONUT_COLORS_DARK", () => {
  it("contains hex color palette entries for both themes", () => {
    for (const palette of [DONUT_COLORS_LIGHT, DONUT_COLORS_DARK]) {
      expect(palette.length).toBeGreaterThanOrEqual(6);
      for (const color of palette) {
        expect(color).toMatch(/^#[0-9a-f]{6}$/i);
      }
    }
  });

  it("light and dark palettes have the same length", () => {
    expect(DONUT_COLORS_LIGHT.length).toBe(DONUT_COLORS_DARK.length);
  });
});
