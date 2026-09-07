import { formatPercentNullable } from "@/utils/formatters";
import { useTranslation } from "react-i18next";

export function UsageCapMarker({ cap }: { cap?: number | null }) {
  const { t } = useTranslation();
  if (cap == null) return null;
  const remaining = Math.max(0, Math.min(100, 100 - cap));
  const label = t("accounts.usageCaps.marker", {
    defaultValue: "Cap: {{cap}}% used ({{remaining}}% remaining)", cap, remaining,
  });
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className="absolute inset-y-0 left-0 z-10 border-r-2 border-background bg-zinc-500"
      style={{ width: `${remaining}%` }}
    />
  );
}

export function UsageCapValue({ percent, cap }: { percent: number | null; cap?: number | null }) {
  const { t } = useTranslation();
  const reserved = cap == null ? 0 : 100 - cap;
  const usable = percent == null || cap == null ? null : Math.max(0, percent - reserved);
  const remainingLabel = formatPercentNullable(percent, 1);
  if (usable == null) return remainingLabel;
  return t("accounts.usageCaps.usableRemaining", {
    defaultValue: "{{remaining}} ({{usable}})",
    remaining: remainingLabel,
    usable: formatPercentNullable(usable, 1),
  });
}
