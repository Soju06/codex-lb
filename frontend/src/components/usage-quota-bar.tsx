import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";
import { quotaBarColor, quotaBarTrack } from "@/utils/account-status";

import { quotaBreakdown } from "@/utils/quota";

export function UsageQuotaBar({ percent, cap }: {
  percent: number | null;
  cap?: number | null;
}) {
  const { t } = useTranslation();
  const { provider, reserved, usable } = quotaBreakdown(percent ?? 0, cap);
  const summary = t("accounts.usageLimit.quotaSummary", {
    usable: Number(usable.toFixed(2)), reserved: Number(reserved.toFixed(2)),
  });
  const providerLabel = t("accounts.usageLimit.providerRemaining", { provider: Number(provider.toFixed(2)) });
  return (
    <div className="min-w-0 space-y-1" title={percent === null ? undefined : `${providerLabel}; ${summary}`}>
      <div role="img" aria-label={percent === null ? t("common.states.unavailable") : `${providerLabel}; ${summary}`}
        className={cn("relative h-1.5 w-full overflow-hidden rounded-full", quotaBarTrack(usable))}>
        <div className={cn("absolute inset-y-0 left-0 transition-colors", quotaBarColor(usable))}
          style={{ left: String(reserved) + "%", width: `${percent === null ? 0 : usable}%` }} />
        {reserved > 0 ? <div className="absolute inset-y-0 left-0 bg-muted-foreground/30"
          style={{ width: `${reserved}%`, backgroundImage: "repeating-linear-gradient(135deg, transparent, transparent 2px, currentColor 2px, currentColor 3px)", opacity: 0.45 }} /> : null}
      </div>
    </div>
  );
}


export function UsageQuotaSummary({ percent, cap }: { percent: number | null; cap?: number | null }) {
  const { t } = useTranslation();
  if (cap == null || percent === null) return null;
  const { reserved, usable } = quotaBreakdown(percent, cap);
  const label = t("accounts.usageLimit.quotaShortSummary", {
    reserved: Number(reserved.toFixed(2)), usable: Number(usable.toFixed(2)),
  });
  return <p className="truncate text-[10px] leading-tight tracking-tight tabular-nums text-muted-foreground" title={label}>{label}</p>;
}
