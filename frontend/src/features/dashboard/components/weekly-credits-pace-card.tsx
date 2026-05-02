import { Gauge } from "lucide-react";

import type { WeeklyCreditPace } from "@/features/dashboard/utils";
import { cn } from "@/lib/utils";
import { formatCompactNumber } from "@/utils/formatters";

export type WeeklyCreditsPaceCardProps = {
  pace: WeeklyCreditPace | null;
};

function formatPercent(value: number): string {
  return `${Math.round(value)}%`;
}

function formatApproxPercent(value: number): string {
  return `~${Math.round(value)}%`;
}

function formatProAccountEquivalent(value: number): string {
  if (value < 1) {
    return value >= 0.1 ? value.toFixed(2) : value.toFixed(3);
  }
  return value < 10 ? value.toFixed(1) : value.toFixed(0);
}

function formatDelta(value: number): string {
  const rounded = Math.round(Math.abs(value));
  return `${rounded} pp`;
}

function statusLabel(pace: WeeklyCreditPace): string {
  if (pace.status === "on_track") return "On pace";
  const direction = pace.deltaPercent > 0 ? "too fast" : "under pace";
  return `${formatDelta(pace.deltaPercent)} ${direction}`;
}

function planLine(pace: WeeklyCreditPace): string {
  const magnitude = formatCompactNumber(Math.abs(pace.overPlanCredits));
  if (pace.overPlanCredits > 0) return `${magnitude} credits over plan`;
  if (pace.overPlanCredits < 0) return `${magnitude} credits under plan`;
  return "0 credits off plan";
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

function breakEvenLine(pace: WeeklyCreditPace): string {
  if (pace.overPlanCredits <= 0) {
    return "No pause needed";
  }
  if (pace.pauseForBreakEvenHours == null) {
    return "Until reset to break even";
  }
  return `${formatDurationHours(pace.pauseForBreakEvenHours)} to break even`;
}

function proAccountsLine(pace: WeeklyCreditPace): string | null {
  if (!pace.proAccountsToCoverOverPlan || pace.proAccountEquivalentToCoverOverPlan == null) {
    return null;
  }
  const equivalent = formatProAccountEquivalent(pace.proAccountEquivalentToCoverOverPlan);
  const roundedLabel = pace.proAccountsToCoverOverPlan === 1 ? "account" : "accounts";
  return `${equivalent}x Pro weekly pool (~${pace.proAccountsToCoverOverPlan} ${roundedLabel})`;
}

function throttleLine(pace: WeeklyCreditPace): string | null {
  if (pace.throttleToPercent == null || pace.reduceByPercent == null) {
    return null;
  }
  return `${formatApproxPercent(pace.reduceByPercent)} less parallel weekly-credit load`;
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
  const plannedMarkerLeft = Math.max(0, Math.min(100, pace.scheduledUsedPercent));
  const throttle = throttleLine(pace);
  const proAccounts = proAccountsLine(pace);

  return (
    <section className="rounded-xl border bg-card p-5" aria-label="Weekly credits pace">
      <div className="mb-4 flex items-center justify-between gap-3">
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
              <p className="text-muted-foreground">Planned by now</p>
              <p className="mt-1 text-sm font-semibold tabular-nums">{formatPercent(pace.scheduledUsedPercent)}</p>
            </div>
            <div className="min-w-0 rounded-md bg-muted/30 px-3 py-2">
              <p className="text-muted-foreground">Over plan</p>
              <p className="mt-1 text-sm font-semibold tabular-nums">{statusLabel(pace)}</p>
            </div>
          </div>
          <div className="relative h-1.5 rounded-full bg-muted">
            <div
              className={cn(
                "h-full rounded-full",
                pace.status === "danger" ? "bg-red-500" : pace.status === "ahead" ? "bg-amber-500" : "bg-primary",
              )}
              style={{ width: `${actualBarWidth}%` }}
            />
            <div
              className="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 rounded-full bg-foreground/70"
              style={{ left: `${plannedMarkerLeft}%` }}
            />
          </div>
          <div className="flex items-center justify-between gap-3 text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <span className="h-1.5 w-4 rounded-full bg-primary" />
              Actual
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-3 w-0.5 rounded-full bg-foreground/70" />
              Plan marker
            </span>
          </div>
        </div>

        <div className="rounded-lg border bg-background/60 px-3 py-2 text-xs">
          <p className="font-medium">Recovery options</p>
          <div className="mt-2 grid gap-1.5">
            <div className="flex items-baseline justify-between gap-3">
              <span className="shrink-0 text-muted-foreground">Pause</span>
              <span className="min-w-0 text-right tabular-nums">{breakEvenLine(pace)}</span>
            </div>
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
          <p className="mt-2 text-muted-foreground">{planLine(pace)}</p>
          <p className="mt-1 text-muted-foreground">
            {formatCompactNumber(pace.totalActualRemainingCredits)} left vs{" "}
            {formatCompactNumber(pace.totalExpectedRemainingCredits)} scheduled
          </p>
        </div>
      </div>
    </section>
  );
}
