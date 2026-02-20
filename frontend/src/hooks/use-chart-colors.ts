import { useMemo } from "react";

import { useThemeStore } from "@/hooks/use-theme";

const CHART_VARS = [
  "--chart-1",
  "--chart-2",
  "--chart-3",
  "--chart-4",
  "--chart-5",
] as const;

const FALLBACK = ["#3b82f6", "#8b5cf6", "#10b981", "#f59e0b", "#ec4899"];

let _canvas: CanvasRenderingContext2D | null = null;

function getCanvasCtx(): CanvasRenderingContext2D | null {
  if (!_canvas) {
    _canvas = document.createElement("canvas").getContext("2d");
  }
  return _canvas;
}

function resolveColor(raw: string, fallback: string): string {
  if (!raw) return fallback;
  const ctx = getCanvasCtx();
  if (!ctx) return fallback;
  ctx.fillStyle = "#000000";
  ctx.fillStyle = raw;
  return ctx.fillStyle === "#000000" && raw !== "#000000" ? fallback : ctx.fillStyle;
}

/**
 * Returns the 5 theme chart colors (`--chart-1` … `--chart-5`) resolved to
 * hex strings so they can be used in Recharts SVG attributes.
 * Re‑resolves automatically when the theme changes.
 */
export function useChartColors(): string[] {
  const theme = useThemeStore((s) => s.theme);
  return useMemo(() => {
    if (typeof document === "undefined") return FALLBACK;
    if (!theme) return FALLBACK;
    const style = getComputedStyle(document.documentElement);
    return CHART_VARS.map((name, i) =>
      resolveColor(style.getPropertyValue(name).trim(), FALLBACK[i]),
    );
  }, [theme]);
}
