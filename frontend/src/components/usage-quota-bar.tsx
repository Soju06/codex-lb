import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";
import { quotaBarColor, quotaBarTrack } from "@/utils/account-status";
import { quotaBreakdown } from "@/utils/quota";

const round = (value: number) => Number(value.toFixed(2));

// Hatches the reserved share at the left of the bar and colors the rest by what Codex LB can use.
export function UsageQuotaBar({ percent, cap, testId, "aria-label": ariaLabel }: {
  percent: number | null;
  cap?: number | null;
  testId?: string;
  "aria-label"?: string;
}) {
  const { t } = useTranslation();
  const { provider, reserved, usable } = quotaBreakdown(percent ?? 0, cap);
  const description = percent === null
    ? t("common.states.unavailable")
    : `${t("accounts.usageLimit.providerRemaining", { provider: round(provider) })}; ${t("accounts.usageLimit.quotaSummary", {
      usable: round(usable), reserved: round(reserved),
    })}`;
  return (
    <div role="img" aria-label={ariaLabel ? `${ariaLabel}; ${description}` : description} data-testid={testId}
      title={percent === null ? undefined : description}
      className={cn("relative h-1.5 w-full min-w-0 overflow-hidden rounded-full", quotaBarTrack(usable))}>
      <div className={cn("absolute inset-y-0 transition-colors", quotaBarColor(usable))}
        style={{ left: `${reserved}%`, width: `${usable}%` }} />
      {reserved > 0 ? <div className="absolute inset-y-0 left-0 bg-muted-foreground/30"
        style={{ width: `${reserved}%`, backgroundImage: "repeating-linear-gradient(135deg, transparent, transparent 2px, currentColor 2px, currentColor 3px)", opacity: 0.45 }} /> : null}
    </div>
  );
}

export function UsageQuotaSummary({ percent, cap }: { percent: number | null; cap?: number | null }) {
  const { t } = useTranslation();
  if (cap == null || percent === null) return null;
  const { reserved, usable } = quotaBreakdown(percent, cap);
  const label = t("accounts.usageLimit.quotaShortSummary", { reserved: round(reserved), usable: round(usable) });
  return <p className="truncate text-[10px] leading-tight tracking-tight tabular-nums text-muted-foreground" title={label}>{label}</p>;
}
