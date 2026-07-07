import { Gauge } from "lucide-react";

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

function statusLabel(pace: WeeklyCreditPace): string {
  const deltaPercent = pace.smoothedDeltaPercent ?? pace.deltaPercent;
  if (pace.status === "on_track") return "On pace";
  if (pace.status === "danger" && pace.projectedShortfallCredits > 0 && deltaPercent <= 0) {
    return "Recent burn shortfall";
  }
  const direction = deltaPercent > 0 ? "over planned usage" : "below planned usage";
  return `${formatSignedPercent(deltaPercent)} ${direction}`;
}

function scheduleGapLine(pace: WeeklyCreditPace): string {
  const scheduleGapCredits = pace.smoothedScheduleGapCredits ?? pace.scheduleGapCredits;
  const deltaPercent = pace.smoothedDeltaPercent ?? pace.deltaPercent;
  const smoothingMinutes = pace.paceGapSmoothingMinutes ?? 0;
  const suffix = smoothingMinutes > 0 ? ` over ${formatDurationHours(smoothingMinutes / 60)}` : " now";
  if (scheduleGapCredits > 0) {
    return `${formatCompactNumber(scheduleGapCredits)} credits over planned usage${suffix}`;
  }
  if (deltaPercent < 0) {
    return `${formatSignedPercent(deltaPercent)} below planned usage${suffix}`;
  }
  return "On the current linear weekly schedule";
}

function forecastLine(pace: WeeklyCreditPace): string {
  if (pace.projectedShortfallCredits > 0) {
    return `${formatCompactNumber(pace.projectedShortfallCredits)} credits projected short before reset`;
  }
  if (pace.forecastBurnRateCreditsPerHour === 0) {
    return "No weekly shortfall projected at recent pace";
  }
  if (pace.projectedMinimumRemainingCredits != null) {
    return `${formatCompactNumber(pace.projectedMinimumRemainingCredits)} credits projected low-water mark`;
  }
  return "Pool covers recent pace through upcoming resets";
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

function breakEvenLine(pace: WeeklyCreditPace): string | null {
  if (pace.projectedShortfallCredits <= 0) {
    return null;
  }
  if (pace.pauseForBreakEvenHours == null) {
    return "Until reset";
  }
  return `${formatDurationHours(pace.pauseForBreakEvenHours)} until reset`;
}

function proAccountsLine(pace: WeeklyCreditPace): string | null {
  const scheduleGapCredits = pace.smoothedScheduleGapCredits ?? pace.scheduleGapCredits;
  const gapCredits =
    pace.projectedShortfallCredits > 0 ? pace.projectedShortfallCredits : Math.max(0, scheduleGapCredits);
  const equivalent =
    pace.proAccountEquivalentToCoverOverPlan ?? (gapCredits > 0 ? gapCredits / PRO_WEEKLY_CAPACITY_CREDITS : null);
  const accounts = pace.proAccountsToCoverOverPlan ?? (gapCredits > 0 ? Math.ceil(gapCredits / PRO_WEEKLY_CAPACITY_CREDITS) : null);

  if (!accounts || equivalent == null) {
    return null;
  }
  const roundedLabel = accounts === 1 ? "account" : "accounts";
  return `${formatProAccountEquivalent(equivalent)}x Pro weekly pool (~${accounts} ${roundedLabel})`;
}

function throttleLine(pace: WeeklyCreditPace): string | null {
  if (pace.throttleToPercent == null || pace.reduceByPercent == null) {
    return null;
  }
  return `Reduce ongoing weekly-credit load by ${formatApproxPercent(pace.reduceByPercent)}`;
}

export function WeeklyCreditsPaceCard({ pace }: WeeklyCreditsPaceCardProps) {
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
  const throttle = throttleLine(pace);
  const proAccounts = proAccountsLine(pace);
  const breakEven = breakEvenLine(pace);
  const smoothedScheduleGapCredits = pace.smoothedScheduleGapCredits ?? pace.scheduleGapCredits;
  const showRecommendations =
    smoothedScheduleGapCredits > 0 ||
    pace.projectedShortfallCredits > 0 ||
    Boolean(breakEven) ||
    Boolean(throttle) ||
    Boolean(proAccounts);

  return (
    <section className="rounded-xl border bg-card p-5" aria-label="Weekly credits pace">
      <div className="mb-4 flex justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">Weekly credits pace</h3>
        </div>
        <div className={cn("flex h-9 w-9 items-center justify-center rounded-lg", statusClass)}>
          <Gauge className="h-4 w-4" aria-hidden="true" />
        </div>
      </div>

      <div className="space-y-4">
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div className="min-w-0 rounded-md bg-muted/30 px-3 py-2">
              <p className="text-muted-foreground">Used now</p>
              <p className="mt-1 text-sm font-semibold tabular-nums">{formatPercent(pace.actualUsedPercent)}</p>
            </div>
            <div className="min-w-0 rounded-md bg-muted/30 px-3 py-2">
              <p className="text-muted-foreground">Scheduled by now</p>
              <p className="mt-1 text-sm font-semibold tabular-nums">{formatPercent(pace.scheduledUsedPercent)}</p>
            </div>
            <div className="min-w-0 rounded-md bg-muted/30 px-3 py-2">
              <p className="text-muted-foreground">Pace gap</p>
              <p className="mt-1 text-sm font-semibold tabular-nums">{statusLabel(pace)}</p>
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
              Actual
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-3 w-0.5 rounded-full bg-foreground/70" />
              Schedule marker
            </span>
          </div>
          <div className="rounded-lg border bg-background/60 px-3 py-2 text-xs text-muted-foreground">
            <p>{scheduleGapLine(pace)}</p>
            <p className="mt-1">{forecastLine(pace)}</p>
          </div>
        </div>

        {showRecommendations ? (
          <div className="rounded-lg border bg-background/60 px-3 py-2 text-xs">
            <p className="font-medium">Recommendations</p>
            <div className="mt-2 grid gap-1.5">
              {breakEven ? (
                <div className="flex items-baseline justify-between gap-3">
                  <span className="shrink-0 text-muted-foreground">Pause</span>
                  <span className="min-w-0 text-right tabular-nums">{breakEven}</span>
                </div>
              ) : null}
              {throttle ? (
                <div className="flex items-baseline justify-between gap-3">
                  <span className="shrink-0 text-muted-foreground">Throttle</span>
                  <span className="min-w-0 text-right tabular-nums">{throttle}</span>
                </div>
              ) : null}
              {proAccounts ? (
                <div className="flex items-baseline justify-between gap-3">
                  <span className="shrink-0 text-muted-foreground">Add capacity</span>
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
