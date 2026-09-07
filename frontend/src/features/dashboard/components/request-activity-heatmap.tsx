import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { RequestActivityDay } from "@/features/dashboard/schemas";
import { SpinnerBlock } from "@/components/ui/spinner";
import { buildRequestActivityCalendar } from "@/features/dashboard/components/request-activity-heatmap-utils";
import { cn } from "@/lib/utils";

const ACTIVITY_LEVEL_CLASSES = [
  "bg-muted/70",
  "bg-primary/20",
  "bg-primary/40",
  "bg-primary/60",
  "bg-primary",
] as const;

export type RequestActivityHeatmapProps = {
  days?: RequestActivityDay[];
  isLoading?: boolean;
  error?: Error | null;
};

export function RequestActivityHeatmap({
  days = [],
  isLoading = false,
  error = null,
}: RequestActivityHeatmapProps) {
  const { t } = useTranslation();
  const calendar = useMemo(
    () => buildRequestActivityCalendar(days),
    [days],
  );
  const weekCount = calendar.weeks.length;
  const gridStyle = { gridTemplateColumns: `repeat(${weekCount}, minmax(0, 1fr))` };

  return (
    <section
      className="min-w-0 rounded-xl border bg-card p-5"
      aria-label={t("dashboard.requestActivity.title")}
    >
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">{t("dashboard.requestActivity.title")}</h3>
        </div>
      </div>

      {isLoading && days.length === 0 ? (
        <SpinnerBlock />
      ) : error ? (
        <p className="text-sm text-destructive" role="alert">
          {error.message || t("dashboard.requestActivity.error")}
        </p>
      ) : (
        <TooltipProvider>
          <div className="min-w-0 overflow-hidden" data-testid="request-activity-heatmap">
            <div
              className="grid min-w-0 grid-flow-col grid-rows-7 gap-1"
              data-testid="request-activity-calendar"
              style={gridStyle}
            >
              {calendar.weeks.flatMap((week) =>
                week.map((cell, index) => {
                  if (!cell) {
                    return (
                      <span
                        key={`empty-${calendar.weeks.indexOf(week)}-${index}`}
                        aria-hidden="true"
                        className="aspect-square min-w-0 rounded-[2px]"
                      />
                    );
                  }
                  const tooltip = t("dashboard.requestActivity.tooltip", {
                    date: cell.date,
                    count: cell.requests,
                  });
                  return (
                    <Tooltip key={cell.date}>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          aria-label={tooltip}
                          className={cn(
                            "aspect-square min-w-0 rounded-[2px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
                            ACTIVITY_LEVEL_CLASSES[cell.level],
                          )}
                          data-count={cell.requests}
                          data-date={cell.date}
                          data-level={cell.level}
                          data-testid="request-activity-cell"
                        />
                      </TooltipTrigger>
                      <TooltipContent side="top">{tooltip}</TooltipContent>
                    </Tooltip>
                  );
                }),
              )}
            </div>
            <div className="mt-3 flex items-center justify-end gap-1.5 text-[10px] text-muted-foreground">
              <span>{t("dashboard.requestActivity.less")}</span>
              {ACTIVITY_LEVEL_CLASSES.map((className, level) => (
                <span
                  key={level}
                  aria-hidden="true"
                  className={cn("h-3 w-3 rounded-[2px]", className)}
                />
              ))}
              <span>{t("dashboard.requestActivity.more")}</span>
            </div>
          </div>
        </TooltipProvider>
      )}
    </section>
  );
}
