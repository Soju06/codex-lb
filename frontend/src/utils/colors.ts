import { DONUT_COLORS_DARK, DONUT_COLORS_LIGHT } from "@/utils/constants";

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function adjustHexColor(hex: string, amount: number): string {
  if (!hex.startsWith("#") || hex.length !== 7) {
    return hex;
  }
  const intValue = Number.parseInt(hex.slice(1), 16);
  if (!Number.isFinite(intValue)) {
    return hex;
  }
  const r = (intValue >> 16) & 255;
  const g = (intValue >> 8) & 255;
  const b = intValue & 255;
  const mix = amount >= 0 ? 255 : 0;
  const factor = clamp(Math.abs(amount), 0, 1);
  const toHex = (channel: number): string => clamp(channel, 0, 255).toString(16).padStart(2, "0");
  const next = (channel: number): number => Math.round(channel + (mix - channel) * factor);
  return `#${toHex(next(r))}${toHex(next(g))}${toHex(next(b))}`;
}

export function buildDonutPalette(count: number, isDark = false): string[] {
  const base = [...(isDark ? DONUT_COLORS_DARK : DONUT_COLORS_LIGHT)] as string[];
  if (count <= base.length) {
    return base.slice(0, count);
  }
  const shifts = [0.2, -0.18, 0.32, -0.28];
  const palette = [...base];
  let index = 0;
  while (palette.length < count) {
    const baseColor = base[index % base.length];
    const shift = shifts[index % shifts.length];
    palette.push(adjustHexColor(baseColor, shift));
    index += 1;
  }
  return palette;
}
