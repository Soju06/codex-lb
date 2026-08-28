import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type DistributionMetric = "cost" | "req";

type DistributionMetricToggleProps = {
  metric: DistributionMetric;
  onChange: (metric: DistributionMetric) => void;
};

const OPTIONS: DistributionMetric[] = ["cost", "req"];

export function DistributionMetricToggle({ metric, onChange }: DistributionMetricToggleProps) {
  return (
    <div className="inline-flex rounded-md border bg-muted/30 p-0.5">
      {OPTIONS.map((option) => {
        const isActive = option === metric;

        return (
          <Button
            key={option}
            type="button"
            size="sm"
            variant="ghost"
            aria-pressed={isActive}
            className={cn(
              "h-6 rounded-[5px] px-2 text-[11px] font-semibold uppercase tracking-wide",
              isActive
                ? "bg-background text-foreground shadow-sm hover:bg-background"
                : "text-muted-foreground hover:bg-transparent hover:text-foreground",
            )}
            onClick={() => onChange(option)}
          >
            {option}
          </Button>
        );
      })}
    </div>
  );
}
