import type { TFunction } from "i18next";

import type { MultiSelectOption } from "@/features/dashboard/components/filters/multi-select-filter";
import type { RequestLogUpstreamOption } from "@/features/dashboard/schemas";

/**
 * Request-log Upstream filter options (#1870). The stable upstream ID is the
 * filter value; labels use the current model-source name, add the ID when two
 * sources share a name, and fall back to a localized deleted-source label.
 */
export function buildUpstreamOptions(upstreams: RequestLogUpstreamOption[], t: TFunction): MultiSelectOption[] {
  const nameCounts = new Map<string, number>();
  for (const upstream of upstreams) {
    if (upstream.type === "model_source" && !upstream.isDeleted && upstream.displayName) {
      nameCounts.set(upstream.displayName, (nameCounts.get(upstream.displayName) ?? 0) + 1);
    }
  }
  return upstreams.map((upstream) => ({
    value: upstream.id,
    label: upstreamLabel(upstream, nameCounts, t),
  }));
}

function upstreamLabel(upstream: RequestLogUpstreamOption, nameCounts: Map<string, number>, t: TFunction): string {
  if (upstream.type === "subscription_pool") {
    return t("dashboard.filters.upstreamSubscriptionPool");
  }
  if (upstream.isDeleted) {
    return t("dashboard.filters.upstreamDeletedSource", { id: upstream.id });
  }
  if (!upstream.displayName) {
    return upstream.id;
  }
  if ((nameCounts.get(upstream.displayName) ?? 0) > 1) {
    return `${upstream.displayName} · ${upstream.id}`;
  }
  return upstream.displayName;
}
