import { cn } from "@/lib/utils";
import { quotaBarColor, quotaBarTrack } from "@/utils/account-status";

type MiniQuotaBarProps = {
  percent: number | null;
  testId: string;
  "aria-label"?: string;
};

export function MiniQuotaBar({ percent, testId, "aria-label": ariaLabel }: MiniQuotaBarProps) {
  if (percent === null) {
    return <div aria-hidden="true" data-testid={testId} className="h-1 flex-1 overflow-hidden rounded-full bg-muted" />;
  }
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <>
      <progress
        aria-label={ariaLabel}
        value={clamped}
        max={100}
        data-testid={testId}
        className="sr-only"
      />
      <div
        aria-hidden="true"
        className={cn("h-1 flex-1 overflow-hidden rounded-full", quotaBarTrack(clamped))}
      >
        <div
          data-testid={`${testId}-fill`}
          className={cn("h-full rounded-full", quotaBarColor(clamped))}
          style={{ width: `${clamped}%` }}
        />
      </div>
    </>
  );
}
