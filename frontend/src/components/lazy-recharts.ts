import { createElement, lazy, Suspense, type ComponentType } from "react";
import { Cell as RechartsCell } from "recharts";

type RechartsModule = typeof import("recharts");
type LazyRechartsWrapper = ComponentType<Record<string, unknown>> & { displayName: string };

function lazyRechartsComponent(name: keyof RechartsModule) {
  const LazyComponent = lazy(async () => {
    const module = await import("recharts");
    return { default: module[name] as ComponentType<unknown> };
  });

  const LazyRechartsComponent: LazyRechartsWrapper = (props: Record<string, unknown>) => {
    return createElement(
      Suspense,
      { fallback: null },
      createElement(LazyComponent, props),
    );
  };

  LazyRechartsComponent.displayName = String(name);

  return LazyRechartsComponent;
}

export const Area = lazyRechartsComponent("Area");
export const AreaChart = lazyRechartsComponent("AreaChart");
export const CartesianGrid = lazyRechartsComponent("CartesianGrid");
export const Cell = RechartsCell;
export const Line = lazyRechartsComponent("Line");
export const Pie = lazyRechartsComponent("Pie");
export const PieChart = lazyRechartsComponent("PieChart");
export const ResponsiveContainer = lazyRechartsComponent("ResponsiveContainer");
export const Sector = lazyRechartsComponent("Sector");
export const Tooltip = lazyRechartsComponent("Tooltip");
export const XAxis = lazyRechartsComponent("XAxis");
export const YAxis = lazyRechartsComponent("YAxis");

export type { PieSectorShapeProps, TooltipContentProps } from "recharts";
