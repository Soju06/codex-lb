import { DONUT_COLORS } from "@/utils/constants";

export const CONSUMED_COLOR = "#d3d3d3";

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

export function buildDonutPalette(count: number): string[] {
  const base = [...DONUT_COLORS] as string[];
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

export type DonutGradientItem = {
  value: number;
  color?: string | null;
};

export function buildDonutGradient(items: DonutGradientItem[], total: number): string {
  if (!items.length || total <= 0) {
    return `conic-gradient(${CONSUMED_COLOR} 0 100%)`;
  }

  const values = items.map((item) => Math.max(0, item.value || 0));
  const remainingTotal = values.reduce((acc, value) => acc + value, 0);
  if (remainingTotal <= 0) {
    return `conic-gradient(${CONSUMED_COLOR} 0 100%)`;
  }

  const positiveValues = values.filter((value) => value > 0);
  const minPositive = positiveValues.length > 0 ? Math.min(...positiveValues) : 0;
  const fallback = Number.isFinite(minPositive) && minPositive > 0 ? minPositive * 0.05 : 0;
  const displayValues =
    fallback > 0 ? values.map((value) => (value > 0 ? value : fallback)) : values;
  const displayTotal = displayValues.reduce((acc, value) => acc + value, 0);
  const remainingPercentTotal = Math.min(100, ((displayTotal > 0 ? remainingTotal : 0) / total) * 100);

  let start = 0;
  const segments = displayValues.map((value, index) => {
    const percent = displayTotal > 0 ? (value / displayTotal) * remainingPercentTotal : 0;
    const end = start + percent;
    const color = items[index]?.color || CONSUMED_COLOR;
    const segment = `${color} ${start}% ${end}%`;
    start = end;
    return segment;
  });

  if (remainingPercentTotal < 100) {
    segments.push(`${CONSUMED_COLOR} ${start}% 100%`);
  }
  return `conic-gradient(${segments.join(", ")})`;
}
