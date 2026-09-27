import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";

import type { AccountSummary } from "@/features/accounts/schemas";
import { useDateDisplayFormatStore } from "@/hooks/use-date-format";
import { cn } from "@/lib/utils";
import { formatDateTimeInline } from "@/utils/formatters";

const AccountClock = createContext<number | null>(null);

export function AccountClockProvider({ children }: { children: ReactNode }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  return <AccountClock.Provider value={now}>{children}</AccountClock.Provider>;
}

export function AccountSubscription({
  account,
  compact = false,
}: {
  account: AccountSummary;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const [mountedAt] = useState(Date.now);
  const now = useContext(AccountClock) ?? mountedAt;
  const dateFormat = useDateDisplayFormatStore((s) => s.dateDisplayFormat);
  const end = account.subscription?.activeUntil;
  const endMs = end ? Date.parse(end) : NaN;
  const known = Number.isFinite(endMs);
  const remaining = endMs - now;
  const checked = account.subscription?.lastCheckedAt;
  const duration =
    remaining >= 86_400_000
      ? t("formatters.duration.daysHours", {
          days: Math.floor(remaining / 86_400_000),
          hours: Math.floor(remaining / 3_600_000) % 24,
        })
      : remaining >= 3_600_000
        ? t("formatters.duration.hoursMinutes", {
            hours: Math.floor(remaining / 3_600_000),
            minutes: Math.floor(remaining / 60_000) % 60,
          })
        : t("formatters.duration.minutes", {
            count: Math.max(1, Math.ceil(remaining / 60_000)),
          });

  if (compact) {
    const shortDuration = `${String(Math.floor(Math.max(0, remaining) / 86_400_000)).padStart(2, "0")}d ${String(Math.floor(Math.max(0, remaining) / 3_600_000) % 24).padStart(2, "0")}h`;
    const summary = !known
      ? t("accounts.subscription.unknown")
      : remaining <= 0
        ? t("accounts.subscription.elapsed")
        : t("accounts.subscription.remaining", { duration: shortDuration });
    const description = [
      `${t("accounts.subscription.title")}: ${summary}`,
      known
        ? `${t("accounts.subscription.until")}: ${formatDateTimeInline(end, dateFormat)}`
        : null,
      checked
        ? `${t("accounts.subscription.checked")}: ${formatDateTimeInline(checked, dateFormat)}`
        : null,
      t(
        known
          ? "accounts.subscription.recordedHint"
          : "accounts.subscription.unknownHint",
      ),
    ]
      .filter(Boolean)
      .join("\n");
    return (
      <span
        data-testid="account-plan-remaining"
        className={cn(
          "whitespace-nowrap text-[11px] font-medium tabular-nums",
          known &&
            remaining <= 3 * 86_400_000 &&
            "text-amber-600 dark:text-amber-400",
        )}
        title={description}
        aria-label={description}
      >
        {!known
          ? t("accounts.subscription.unknown")
          : remaining <= 0
            ? t("accounts.subscription.elapsedShort")
            : shortDuration}
      </span>
    );
  }

  return (
    <section
      className="min-w-0 space-y-2 rounded-lg border bg-muted/30 p-4"
      aria-label={t("accounts.subscription.title")}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-semibold text-muted-foreground">
          {t("accounts.subscription.title")}
        </h3>
        <span
          className={cn(
            "text-sm font-semibold tabular-nums",
            known &&
              remaining <= 3 * 86_400_000 &&
              "text-amber-600 dark:text-amber-400",
          )}
        >
          {!known
            ? t("accounts.subscription.unknown")
            : remaining <= 0
              ? t("accounts.subscription.elapsed")
              : t("accounts.subscription.remaining", { duration })}
        </span>
      </div>
      <dl className="space-y-1 text-xs text-muted-foreground">
        {known ? (
          <div className="flex flex-wrap justify-between gap-x-2">
            <dt>{t("accounts.subscription.until")}</dt>
            <dd>{formatDateTimeInline(end, dateFormat)}</dd>
          </div>
        ) : null}
        {checked ? (
          <div className="flex flex-wrap justify-between gap-x-2">
            <dt>{t("accounts.subscription.checked")}</dt>
            <dd>{formatDateTimeInline(checked, dateFormat)}</dd>
          </div>
        ) : null}
      </dl>
      <p className="text-[11px] text-muted-foreground">
        {t(
          known
            ? "accounts.subscription.recordedHint"
            : "accounts.subscription.unknownHint",
        )}
      </p>
    </section>
  );
}
