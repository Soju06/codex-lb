import type { DistributionMetric } from "./distribution-metric-toggle";
import { formatCompactNumber } from "@/utils/formatters";

export function formatDistributionMetricValue(
  value: number,
  metric: DistributionMetric,
): string {
  const formatted = formatCompactNumber(value);
  return metric === "cost" ? `$${formatted}` : formatted;
}
