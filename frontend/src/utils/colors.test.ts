import { describe, expect, it } from "vitest";

import { DONUT_COLORS } from "@/utils/constants";
import {
  CONSUMED_COLOR,
  adjustHexColor,
  buildDonutGradient,
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
  it("uses base palette for small counts", () => {
    const palette = buildDonutPalette(3);
    expect(palette).toEqual(DONUT_COLORS.slice(0, 3));
  });

  it("extends palette for large counts", () => {
    const palette = buildDonutPalette(10);
    expect(palette).toHaveLength(10);
    expect(palette.slice(0, DONUT_COLORS.length)).toEqual([...DONUT_COLORS]);
  });
});

describe("buildDonutGradient", () => {
  it("returns consumed gradient for empty items", () => {
    expect(buildDonutGradient([], 100)).toBe(`conic-gradient(${CONSUMED_COLOR} 0 100%)`);
  });

  it("builds gradient with segments and consumed remainder", () => {
    const gradient = buildDonutGradient(
      [
        { value: 60, color: "#111111" },
        { value: 20, color: "#222222" },
      ],
      100,
    );

    expect(gradient.startsWith("conic-gradient(")).toBe(true);
    expect(gradient).toContain("#111111");
    expect(gradient).toContain("#222222");
    expect(gradient).toContain(CONSUMED_COLOR);
  });

  it("renders tiny fallback segments for zero-value items when needed", () => {
    const gradient = buildDonutGradient(
      [
        { value: 0, color: "#111111" },
        { value: 10, color: "#222222" },
      ],
      20,
    );

    expect(gradient).toContain("#111111");
    expect(gradient).toContain("#222222");
  });
});
