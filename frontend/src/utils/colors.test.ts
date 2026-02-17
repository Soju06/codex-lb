import { describe, expect, it } from "vitest";

import { DONUT_COLORS_DARK, DONUT_COLORS_LIGHT } from "@/utils/constants";
import {
  adjustHexColor,
  buildDonutPalette,
} from "@/utils/colors";

describe("adjustHexColor", () => {
  it("lightens and darkens valid hex colors", () => {
    expect(adjustHexColor("#000000", 1)).toBe("#ffffff");
    expect(adjustHexColor("#ffffff", -1)).toBe("#000000");
    expect(adjustHexColor("#000000", 0.5)).toBe("#808080");
  });

  it("returns input for invalid hex values", () => {
    expect(adjustHexColor("not-a-color", 0.5)).toBe("not-a-color");
    expect(adjustHexColor("#12", 0.5)).toBe("#12");
  });
});

describe("buildDonutPalette", () => {
  it("uses light palette by default for small counts", () => {
    const palette = buildDonutPalette(3);
    expect(palette).toEqual(DONUT_COLORS_LIGHT.slice(0, 3));
  });

  it("uses dark palette when isDark is true", () => {
    const palette = buildDonutPalette(3, true);
    expect(palette).toEqual(DONUT_COLORS_DARK.slice(0, 3));
  });

  it("extends palette for large counts", () => {
    const palette = buildDonutPalette(10);
    expect(palette).toHaveLength(10);
    expect(palette.slice(0, DONUT_COLORS_LIGHT.length)).toEqual([...DONUT_COLORS_LIGHT]);
  });
});
