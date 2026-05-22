import { cn } from "@/lib/utils";
import { quotaBarColor, quotaBarTrack } from "@/utils/account-status";

export function MiniQuotaBar({ percent, testId }: { percent: number | null; testId: string }) {
  if (percent === null) {
    return <div data-testid={testId} className="h-1 flex-1 overflow-hidden rounded-full bg-muted" />;
  }
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div data-testid={testId} className={cn("h-1 flex-1 overflow-hidden rounded-full", quotaBarTrack(clamped))}>
      <div
        data-testid={`${testId}-fill`}
        className={cn("h-full rounded-full", quotaBarColor(clamped))}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}
