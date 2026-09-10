import { Gauge } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { WeeklyCreditPace, WeeklyCreditRunwayStatus } from "@/features/dashboard/utils";
import { useDateDisplayFormatStore } from "@/hooks/use-date-format";
import { cn } from "@/lib/utils";
import { formatCompactNumber, formatDateTimeInline, formatModelLabel } from "@/utils/formatters";

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

function formatHeadroomPercent(value: number): string {
  const clamped = Math.max(0, value);
  return clamped > 0 && clamped < 10 ? `${clamped.toFixed(1)}%` : `${Math.round(clamped)}%`;
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
  const window = smoothingMinutes > 0 ? formatDurationHours(smoothingMinutes / 60, t) : null;
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

function formatDurationHours(hours: number, t: ReturnType<typeof useTranslation>["t"]): string {
  const totalMinutes = Math.max(1, Math.ceil(hours * 60));
  const days = Math.floor(totalMinutes / 1440);
  const hoursPart = Math.floor((totalMinutes % 1440) / 60);
  const minutesPart = totalMinutes % 60;

  if (days > 0) {
    return hoursPart > 0
      ? t("formatters.duration.daysHours", { days, hours: hoursPart })
      : t("formatters.duration.days", { count: days });
  }
  if (hoursPart > 0) {
    return minutesPart > 0
      ? t("formatters.duration.hoursMinutes", { hours: hoursPart, minutes: minutesPart })
      : t("formatters.duration.hours", { count: hoursPart });
  }
  return t("formatters.duration.minutes", { count: minutesPart });
}

function breakEvenLine(pace: WeeklyCreditPace, t: ReturnType<typeof useTranslation>["t"]): string | null {
  if (pace.projectedShortfallCredits <= 0) {
    return null;
  }
  if (pace.pauseForBreakEvenHours == null) {
    return t("dashboard.weeklyPace.recommendations.untilReset");
  }
  return t("dashboard.weeklyPace.recommendations.pauseUntilReset", {
    duration: formatDurationHours(pace.pauseForBreakEvenHours, t),
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

const VERDICT_LABEL_KEYS: Record<WeeklyCreditRunwayStatus, string> = {
  safe: "dashboard.weeklyPace.verdict.safe",
  tight: "dashboard.weeklyPace.verdict.tight",
  runs_dry: "dashboard.weeklyPace.verdict.runsDry",
};

const VERDICT_BADGE_CLASSES: Record<WeeklyCreditRunwayStatus, string> = {
  safe: "border-border bg-muted/40 text-muted-foreground",
  tight: "border-amber-500/25 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  runs_dry: "border-red-500/25 bg-red-500/10 text-red-700 dark:text-red-300",
};

const VERDICT_FILL_CLASSES: Record<WeeklyCreditRunwayStatus, string> = {
  safe: "bg-primary/50",
  tight: "bg-amber-500/60",
  runs_dry: "bg-red-500/60",
};

const VERDICT_MARKER_CLASSES: Record<WeeklyCreditRunwayStatus, string> = {
  safe: "bg-primary",
  tight: "bg-amber-500",
  runs_dry: "bg-red-500",
};

const TIMELINE_MIN_HORIZON_HOURS = 48;

type RunwayTick = {
  leftPercent: number;
  creditsReturned: number;
  atLabel: string;
};

type RunwayTimeline = {
  etaAtLabel: string | null;
  horizonHours: number;
  etaPercent: number | null;
  resetTicks: RunwayTick[];
};

function buildRunwayTimeline(
  etaHours: number | null,
  reliefHours: number | null,
  resetEvents: WeeklyCreditPace["resetEvents"],
  formatAbsolute: (epochMs: number) => string,
): RunwayTimeline {
  const nowMs = Date.now();
  // A tiny positive burn rate can push the ETA past the representable Date
  // range; formatting would throw, so the absolute label is skipped instead.
  const etaDate = etaHours != null ? new Date(nowMs + etaHours * 3_600_000) : null;
  const etaAtLabel = etaDate != null && Number.isFinite(etaDate.getTime()) ? formatAbsolute(etaDate.getTime()) : null;
  const upcomingResets = (resetEvents ?? []).flatMap((event): { hoursFromNow: number; atMs: number; creditsReturned: number }[] => {
    const atMs = Date.parse(event.at);
    if (!Number.isFinite(atMs)) {
      return [];
    }
    const hoursFromNow = (atMs - nowMs) / 3_600_000;
    return hoursFromNow < 0 ? [] : [{ hoursFromNow, atMs, creditsReturned: event.creditsReturned }];
  });
  // The horizon stretches to the latest reset event so later relief from the
  // backend's seven-day event window is never silently dropped off the rail.
  const horizonHours = Math.max(
    etaHours ?? 0,
    reliefHours ?? 0,
    ...upcomingResets.map((event) => event.hoursFromNow),
    TIMELINE_MIN_HORIZON_HOURS,
  );
  const etaPercent = etaHours != null ? Math.min(100, Math.max(0, (etaHours / horizonHours) * 100)) : null;
  const resetTicks = upcomingResets.map(
    (event): RunwayTick => ({
      leftPercent: Math.min(100, (event.hoursFromNow / horizonHours) * 100),
      creditsReturned: event.creditsReturned,
      atLabel: formatAbsolute(event.atMs),
    }),
  );
  return { etaAtLabel, horizonHours, etaPercent, resetTicks };
}

function RunwayWeeklyCreditsPaceCard({
  pace,
  runwayStatus,
  headroomPercent,
  headroomCredits,
}: {
  pace: WeeklyCreditPace;
  runwayStatus: WeeklyCreditRunwayStatus;
  headroomPercent: number;
  headroomCredits: number;
}) {
  const { t } = useTranslation();
  const dateDisplayFormat = useDateDisplayFormatStore((state) => state.dateDisplayFormat);

  const burnRate = pace.burnRateRecentCreditsPerHour ?? null;
  const etaHours = pace.depletionEtaHours != null && Number.isFinite(pace.depletionEtaHours) ? pace.depletionEtaHours : null;
  const reliefHours = pace.nextReliefInHours != null && Number.isFinite(pace.nextReliefInHours) ? pace.nextReliefInHours : null;
  const reliefCredits = pace.nextReliefCredits ?? null;
  const saturated =
    pace.saturatedAccountCount != null && pace.accountCount > 0 && pace.saturatedAccountCount === pace.accountCount;
  const runsDry = runwayStatus === "runs_dry";

  // The depletion/reset tooltips are read-only timestamps, so they follow the
  // dashboard's date-display and 12/24-hour preferences via the shared
  // formatter rather than a locale-default Intl instance.
  const { etaAtLabel, horizonHours, etaPercent, resetTicks } = buildRunwayTimeline(
    etaHours,
    reliefHours,
    pace.resetEvents,
    (epochMs) => formatDateTimeInline(new Date(epochMs).toISOString(), dateDisplayFormat),
  );

  const topApiKeys = pace.topApiKeys ?? [];
  const throttleToPercent = runsDry && pace.throttleToPercent != null ? pace.throttleToPercent : null;
  const addProAccounts = pace.addProAccounts != null && pace.addProAccounts > 0 ? pace.addProAccounts : null;
  const showRecommendations = throttleToPercent != null || addProAccounts != null;

  const burnRateLine =
    burnRate != null && burnRate > 0
      ? t(saturated ? "dashboard.weeklyPace.atBurnRateFloor" : "dashboard.weeklyPace.atBurnRate", {
          credits: formatCompactNumber(burnRate),
        })
      : null;

  return (
    <section className="rounded-xl border bg-card p-5" aria-label={t("dashboard.weeklyPace.title")}>
      <div className="mb-4 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">{t("dashboard.weeklyPace.title")}</h3>
        <span
          data-testid="weekly-runway-verdict"
          className={cn(
            "inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-[11px] font-medium",
            VERDICT_BADGE_CLASSES[runwayStatus],
          )}
        >
          {t(VERDICT_LABEL_KEYS[runwayStatus])}
        </span>
      </div>

      <div className="space-y-4">
        <div className="min-h-16">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <p className="text-[1.625rem] font-semibold tabular-nums tracking-[-0.02em]">
              {formatHeadroomPercent(headroomPercent)}
            </p>
            <p className="text-xs tabular-nums text-muted-foreground">
              {t("dashboard.weeklyPace.heroCreditsLeft", { credits: formatCompactNumber(Math.max(0, headroomCredits)) })}
            </p>
          </div>
          <p className="mt-1 text-xs tabular-nums text-muted-foreground">
            {etaHours != null ? (
              <span
                title={etaAtLabel ?? undefined}
                className={cn(runsDry && "font-medium text-red-600 dark:text-red-400")}
              >
                {t("dashboard.weeklyPace.runsOutIn", { duration: formatDurationHours(etaHours, t) })}
              </span>
            ) : burnRate == null ? (
              // null burn rate means "too few recent samples to measure",
              // which must read differently from a measured zero burn.
              <span>{t("dashboard.weeklyPace.burnNotMeasured")}</span>
            ) : (
              <span>{t("dashboard.weeklyPace.steadyState")}</span>
            )}
            {burnRateLine ? <span className="ml-1.5">{burnRateLine}</span> : null}
          </p>
          {saturated ? (
            <p className="mt-1 text-[11px] text-muted-foreground">{t("dashboard.weeklyPace.saturatedFloor")}</p>
          ) : null}
        </div>

        <div className="min-h-5 text-xs">
          {runsDry && etaHours != null && reliefHours != null ? (
            <p className="font-medium tabular-nums text-red-600 dark:text-red-400">
              {t("dashboard.weeklyPace.runsDryBeforeRelief", {
                eta: formatDurationHours(etaHours, t),
                relief: formatDurationHours(reliefHours, t),
              })}
            </p>
          ) : reliefHours != null ? (
            <p className="tabular-nums text-muted-foreground">
              {t("dashboard.weeklyPace.reliefLine", {
                duration: formatDurationHours(reliefHours, t),
                credits: formatCompactNumber(Math.max(0, reliefCredits ?? 0)),
              })}
            </p>
          ) : null}
        </div>

        <div>
          <div className="relative h-1.5 rounded-full bg-muted" data-testid="runway-timeline">
            {etaPercent != null ? (
              <div
                className={cn("h-full rounded-full", VERDICT_FILL_CLASSES[runwayStatus])}
                style={{ width: `${etaPercent}%` }}
              />
            ) : null}
            {resetTicks.map((tick, index) => (
              <div
                key={`reset-${index}`}
                data-testid="runway-reset-tick"
                title={t("dashboard.weeklyPace.resetTickTooltip", {
                  credits: formatCompactNumber(tick.creditsReturned),
                  time: tick.atLabel,
                })}
                className="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 rounded-full bg-foreground/50"
                style={{ left: `${tick.leftPercent}%` }}
              />
            ))}
            {etaPercent != null ? (
              <div
                data-testid="runway-eta-marker"
                title={etaAtLabel ?? undefined}
                className={cn(
                  "absolute top-1/2 h-3 w-0.5 -translate-y-1/2 rounded-full",
                  VERDICT_MARKER_CLASSES[runwayStatus],
                )}
                style={{ left: `${etaPercent}%` }}
              />
            ) : null}
          </div>
          <div className="mt-1 flex items-center justify-between text-[10px] text-muted-foreground">
            <span>{t("dashboard.weeklyPace.timelineNow")}</span>
            <span className="tabular-nums">{formatDurationHours(horizonHours, t)}</span>
          </div>
        </div>

        {topApiKeys.length > 0 ? (
          <div data-testid="runway-attribution">
            <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              {t("dashboard.weeklyPace.attributionTitle")}
            </p>
            <ul className="mt-1.5 space-y-1">
              {topApiKeys.map((apiKey, index) => (
                <li
                  // Prefer the stable wire id; older backends omit it, and key
                  // names are not unique, so name+index disambiguates then.
                  key={apiKey.apiKeyId ?? `${apiKey.name}-${index}`}
                  // The fixed metric columns need ~324px on their own, so
                  // below sm the key name wraps onto its own line instead of
                  // forcing the card past narrow (<375px) viewports.
                  className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-xs text-muted-foreground sm:grid sm:grid-cols-[minmax(0,1fr)_5.5rem_5rem_6.5rem]"
                >
                  <span className="w-full min-w-0 truncate sm:w-auto">{apiKey.name}</span>
                  <span className="tabular-nums sm:text-right">
                    {t("dashboard.weeklyPace.attributionRequests", { value: formatCompactNumber(apiKey.requests) })}
                  </span>
                  <span className="tabular-nums sm:text-right">
                    {t("dashboard.weeklyPace.attributionTokens", { value: formatCompactNumber(apiKey.billableTokens) })}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-right text-foreground/70 sm:flex-none">
                    {formatModelLabel(apiKey.dominantModel, null)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {showRecommendations ? (
          <div className="rounded-lg border bg-background/60 px-3 py-2 text-xs" data-testid="runway-recommendations">
            <p className="font-medium">{t("dashboard.weeklyPace.recommendations.title")}</p>
            <div className="mt-2 grid gap-1.5">
              {throttleToPercent != null ? (
                <div className="flex items-baseline justify-between gap-3">
                  <span className="shrink-0 text-muted-foreground">
                    {t("dashboard.weeklyPace.recommendations.throttleLabel")}
                  </span>
                  <span className="min-w-0 text-right tabular-nums">
                    {t("dashboard.weeklyPace.recommendations.throttleTo", {
                      percent: formatPercent(throttleToPercent),
                    })}
                  </span>
                </div>
              ) : null}
              {addProAccounts != null ? (
                <div className="flex items-baseline justify-between gap-3">
                  <span className="shrink-0 text-muted-foreground">
                    {t("dashboard.weeklyPace.recommendations.addCapacity")}
                  </span>
                  <span className="min-w-0 text-right tabular-nums">
                    {t("dashboard.weeklyPace.recommendations.addProAccounts", { count: addProAccounts })}
                  </span>
                </div>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function LegacyWeeklyCreditsPaceCard({ pace }: { pace: WeeklyCreditPace }) {
  const { t } = useTranslation();
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

export function WeeklyCreditsPaceCard({ pace }: WeeklyCreditsPaceCardProps) {
  if (!pace) {
    return null;
  }
  // The runway layout renders only when the backend sent the full companion
  // set; a partial payload falls back to the legacy layout instead of
  // masking gaps with synthesized stand-in numbers.
  if (pace.runwayStatus != null && pace.headroomPercent != null && pace.headroomCredits != null) {
    return (
      <RunwayWeeklyCreditsPaceCard
        pace={pace}
        runwayStatus={pace.runwayStatus}
        headroomPercent={pace.headroomPercent}
        headroomCredits={pace.headroomCredits}
      />
    );
  }
  return <LegacyWeeklyCreditsPaceCard pace={pace} />;
}
