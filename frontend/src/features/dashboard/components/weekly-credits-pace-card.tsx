import { Gauge } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { WeeklyCreditPace } from "@/features/dashboard/utils";
import { cn } from "@/lib/utils";
import { formatCompactNumber } from "@/utils/formatters";

const PRO_WEEKLY_CAPACITY_CREDITS = 50_400;

export type WeeklyCreditsPaceCardProps = {
  pace: WeeklyCreditPace | null;
};

function formatPercent(value: number): string {
  return `${Math.round(value)}%`;
}

function formatApproxPercent(value: number): string {
  return `~${Math.round(value)}%`;
}

function formatSignedPercent(value: number): string {
  return `${Math.round(Math.abs(value))}%`;
}

function formatProAccountEquivalent(value: number): string {
  if (value < 1) {
    return value >= 0.1 ? value.toFixed(2) : value.toFixed(3);
  }
  return value < 10 ? value.toFixed(1) : value.toFixed(0);
}

function statusLabel(pace: WeeklyCreditPace, t: ReturnType<typeof useTranslation>["t"]): string {
  const deltaPercent = pace.smoothedDeltaPercent ?? pace.deltaPercent;
  if (pace.status === "on_track") return t("dashboard.weeklyPace.status.onPace");
  if (pace.status === "danger" && pace.projectedShortfallCredits > 0 && deltaPercent <= 0) {
    return t("dashboard.weeklyPace.status.recentBurnShortfall");
  }
  return deltaPercent > 0
    ? t("dashboard.weeklyPace.status.overPlanned", { percent: formatSignedPercent(deltaPercent) })
    : t("dashboard.weeklyPace.status.belowPlanned", { percent: formatSignedPercent(deltaPercent) });
}

function scheduleGapLine(pace: WeeklyCreditPace, t: ReturnType<typeof useTranslation>["t"]): string {
  const scheduleGapCredits = pace.smoothedScheduleGapCredits ?? pace.scheduleGapCredits;
  const deltaPercent = pace.smoothedDeltaPercent ?? pace.deltaPercent;
  const smoothingMinutes = pace.paceGapSmoothingMinutes ?? 0;
  const window = smoothingMinutes > 0 ? formatDurationHours(smoothingMinutes / 60) : null;
  if (scheduleGapCredits > 0) {
    return window
      ? t("dashboard.weeklyPace.lines.overPlannedWindow", { credits: formatCompactNumber(scheduleGapCredits), window })
      : t("dashboard.weeklyPace.lines.overPlannedNow", { credits: formatCompactNumber(scheduleGapCredits) });
  }
  if (deltaPercent < 0) {
    return window
      ? t("dashboard.weeklyPace.lines.belowPlannedWindow", { percent: formatSignedPercent(deltaPercent), window })
      : t("dashboard.weeklyPace.lines.belowPlannedNow", { percent: formatSignedPercent(deltaPercent) });
  }
  return t("dashboard.weeklyPace.lines.onSchedule");
}

function forecastLine(pace: WeeklyCreditPace, t: ReturnType<typeof useTranslation>["t"]): string {
  if (pace.projectedShortfallCredits > 0) {
    return t("dashboard.weeklyPace.lines.projectedShortfall", {
      credits: formatCompactNumber(pace.projectedShortfallCredits),
    });
  }
  if (pace.forecastBurnRateCreditsPerHour === 0) {
    return t("dashboard.weeklyPace.lines.noShortfall");
  }
  if (pace.projectedMinimumRemainingCredits != null) {
    return t("dashboard.weeklyPace.lines.lowWaterMark", {
      credits: formatCompactNumber(pace.projectedMinimumRemainingCredits),
    });
  }
  return t("dashboard.weeklyPace.lines.poolCoversPace");
}

function formatDurationHours(hours: number): string {
  const totalMinutes = Math.max(1, Math.ceil(hours * 60));
  const days = Math.floor(totalMinutes / 1440);
  const hoursPart = Math.floor((totalMinutes % 1440) / 60);
  const minutesPart = totalMinutes % 60;

  if (days > 0) {
    return hoursPart > 0 ? `${days}d ${hoursPart}h` : `${days}d`;
  }
  if (hoursPart > 0) {
    return minutesPart > 0 ? `${hoursPart}h ${minutesPart}m` : `${hoursPart}h`;
  }
  return `${minutesPart}m`;
}

function breakEvenLine(pace: WeeklyCreditPace, t: ReturnType<typeof useTranslation>["t"]): string | null {
  if (pace.projectedShortfallCredits <= 0) {
    return null;
  }
  if (pace.pauseForBreakEvenHours == null) {
    return t("dashboard.weeklyPace.recommendations.untilReset");
  }
  return t("dashboard.weeklyPace.recommendations.pauseUntilReset", {
    duration: formatDurationHours(pace.pauseForBreakEvenHours),
  });
}

function proAccountsLine(pace: WeeklyCreditPace, t: ReturnType<typeof useTranslation>["t"]): string | null {
  const scheduleGapCredits = pace.smoothedScheduleGapCredits ?? pace.scheduleGapCredits;
  const gapCredits =
    pace.projectedShortfallCredits > 0 ? pace.projectedShortfallCredits : Math.max(0, scheduleGapCredits);
  const equivalent =
    pace.proAccountEquivalentToCoverOverPlan ?? (gapCredits > 0 ? gapCredits / PRO_WEEKLY_CAPACITY_CREDITS : null);
  const accounts = pace.proAccountsToCoverOverPlan ?? (gapCredits > 0 ? Math.ceil(gapCredits / PRO_WEEKLY_CAPACITY_CREDITS) : null);

  if (!accounts || equivalent == null) {
    return null;
  }
  return t("dashboard.weeklyPace.recommendations.proAccounts", {
    equivalent: formatProAccountEquivalent(equivalent),
    count: accounts,
  });
}

function throttleLine(pace: WeeklyCreditPace, t: ReturnType<typeof useTranslation>["t"]): string | null {
  if (pace.throttleToPercent == null || pace.reduceByPercent == null) {
    return null;
  }
  return t("dashboard.weeklyPace.recommendations.throttle", {
    percent: formatApproxPercent(pace.reduceByPercent),
  });
}

export function WeeklyCreditsPaceCard({ pace }: WeeklyCreditsPaceCardProps) {
  const { t } = useTranslation();
  if (!pace) {
    return null;
  }

  const statusClass =
    pace.status === "danger"
      ? "border-red-500/25 bg-red-500/10 text-red-700 dark:text-red-300"
      : pace.status === "ahead"
        ? "border-amber-500/25 bg-amber-500/10 text-amber-700 dark:text-amber-300"
        : pace.status === "behind"
          ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
          : "border-border bg-muted/40 text-muted-foreground";
  const actualBarWidth = Math.max(0, Math.min(100, pace.actualUsedPercent));
  const scheduledMarkerLeft = Math.max(0, Math.min(100, pace.scheduledUsedPercent));
  const actualBarClass =
    pace.status === "danger" ? "bg-red-500" : pace.status === "ahead" ? "bg-amber-500" : "bg-primary";
  const throttle = throttleLine(pace, t);
  const proAccounts = proAccountsLine(pace, t);
  const breakEven = breakEvenLine(pace, t);
  const smoothedScheduleGapCredits = pace.smoothedScheduleGapCredits ?? pace.scheduleGapCredits;
  const showRecommendations =
    smoothedScheduleGapCredits > 0 ||
    pace.projectedShortfallCredits > 0 ||
    Boolean(breakEven) ||
    Boolean(throttle) ||
    Boolean(proAccounts);

  return (
    <section className="rounded-xl border bg-card p-5" aria-label={t("dashboard.weeklyPace.title")}>
      <div className="mb-4 flex justify-between gap-3">
        <div>
	          <h3 className="text-sm font-semibold">{t("dashboard.weeklyPace.title")}</h3>
        </div>
        <div className={cn("flex h-9 w-9 items-center justify-center rounded-lg", statusClass)}>
          <Gauge className="h-4 w-4" aria-hidden="true" />
        </div>
      </div>

      <div className="space-y-4">
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div className="min-w-0 rounded-md bg-muted/30 px-3 py-2">
	              <p className="text-muted-foreground">{t("dashboard.weeklyPace.usedNow")}</p>
              <p className="mt-1 text-sm font-semibold tabular-nums">{formatPercent(pace.actualUsedPercent)}</p>
            </div>
            <div className="min-w-0 rounded-md bg-muted/30 px-3 py-2">
	              <p className="text-muted-foreground">{t("dashboard.weeklyPace.scheduledByNow")}</p>
              <p className="mt-1 text-sm font-semibold tabular-nums">{formatPercent(pace.scheduledUsedPercent)}</p>
            </div>
            <div className="min-w-0 rounded-md bg-muted/30 px-3 py-2">
	              <p className="text-muted-foreground">{t("dashboard.weeklyPace.paceGap")}</p>
	              <p className="mt-1 text-sm font-semibold tabular-nums">{statusLabel(pace, t)}</p>
            </div>
          </div>
          <div className="relative h-1.5 rounded-full bg-muted">
            <div className={cn("h-full rounded-full", actualBarClass)} style={{ width: `${actualBarWidth}%` }} />
            <div
              className="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 rounded-full bg-foreground/70"
              style={{ left: `${scheduledMarkerLeft}%` }}
            />
          </div>
          <div className="flex items-center justify-between gap-3 text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <span className={cn("h-1.5 w-4 rounded-full", actualBarClass)} />
	              {t("dashboard.weeklyPace.actual")}
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-3 w-0.5 rounded-full bg-foreground/70" />
	              {t("dashboard.weeklyPace.scheduleMarker")}
            </span>
          </div>
          <div className="rounded-lg border bg-background/60 px-3 py-2 text-xs text-muted-foreground">
	            <p>{scheduleGapLine(pace, t)}</p>
	            <p className="mt-1">{forecastLine(pace, t)}</p>
          </div>
        </div>

        {showRecommendations ? (
          <div className="rounded-lg border bg-background/60 px-3 py-2 text-xs">
	            <p className="font-medium">{t("dashboard.weeklyPace.recommendations.title")}</p>
            <div className="mt-2 grid gap-1.5">
              {breakEven ? (
                <div className="flex items-baseline justify-between gap-3">
	                  <span className="shrink-0 text-muted-foreground">{t("dashboard.weeklyPace.recommendations.pause")}</span>
                  <span className="min-w-0 text-right tabular-nums">{breakEven}</span>
                </div>
              ) : null}
              {throttle ? (
                <div className="flex items-baseline justify-between gap-3">
	                  <span className="shrink-0 text-muted-foreground">{t("dashboard.weeklyPace.recommendations.throttleLabel")}</span>
                  <span className="min-w-0 text-right tabular-nums">{throttle}</span>
                </div>
              ) : null}
              {proAccounts ? (
                <div className="flex items-baseline justify-between gap-3">
	                  <span className="shrink-0 text-muted-foreground">{t("dashboard.weeklyPace.recommendations.addCapacity")}</span>
                  <span className="min-w-0 text-right tabular-nums">{proAccounts}</span>
                </div>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
