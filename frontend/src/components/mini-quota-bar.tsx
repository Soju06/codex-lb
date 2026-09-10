import { UsageCapMarker } from "@/components/usage-cap-marker";
import { cn } from "@/lib/utils";
import { quotaBarColor, quotaBarTrack } from "@/utils/account-status";

type MiniQuotaBarProps = {
  percent: number | null;
  testId: string;
  "aria-label"?: string;
  cap?: number | null;
};

export function MiniQuotaBar({ percent, testId, cap, "aria-label": ariaLabel }: MiniQuotaBarProps) {
  if (percent === null) {
    return <div data-testid={testId} className="relative h-1 flex-1 overflow-hidden rounded-full bg-muted"><UsageCapMarker cap={cap} /></div>;
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
        className={cn("relative h-1 flex-1 overflow-hidden rounded-full", quotaBarTrack(clamped))}
      >
        <UsageCapMarker cap={cap} />
        <div
          data-testid={`${testId}-fill`}
          className={cn("h-full rounded-full transition-colors duration-500 motion-reduce:transition-none", quotaBarColor(clamped))}
          style={{ width: `${clamped}%` }}
        />
      </div>
    </>
  );
}
