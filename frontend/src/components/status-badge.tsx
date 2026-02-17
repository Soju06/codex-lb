import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { STATUS_LABELS } from "@/utils/constants";

type StatusValue = "active" | "paused" | "limited" | "exceeded" | "deactivated";

const statusClassMap: Record<StatusValue, string> = {
  active: "bg-emerald-500/15 text-emerald-700 border-emerald-500/20 hover:bg-emerald-500/20 dark:text-emerald-400",
  paused: "bg-amber-500/15 text-amber-700 border-amber-500/20 hover:bg-amber-500/20 dark:text-amber-400",
  limited: "bg-orange-500/15 text-orange-700 border-orange-500/20 hover:bg-orange-500/20 dark:text-orange-400",
  exceeded: "bg-red-500/15 text-red-700 border-red-500/20 hover:bg-red-500/20 dark:text-red-400",
  deactivated: "bg-zinc-500/15 text-zinc-600 border-zinc-500/20 hover:bg-zinc-500/20 dark:text-zinc-400",
};

export type StatusBadgeProps = {
  status: StatusValue | string;
};

export function StatusBadge({ status }: StatusBadgeProps) {
  const normalized = (status || "deactivated") as StatusValue;
  const className = statusClassMap[normalized] ?? statusClassMap.deactivated;
  const label = STATUS_LABELS[normalized] ?? status;

  return (
    <Badge className={cn("gap-1.5", className)} variant="outline">
      <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
      {label}
    </Badge>
  );
}
