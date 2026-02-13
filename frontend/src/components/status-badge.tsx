import { Badge } from "@/components/ui/badge";
import { STATUS_LABELS } from "@/utils/constants";

type StatusValue = "active" | "paused" | "limited" | "exceeded" | "deactivated";

const statusClassMap: Record<StatusValue, string> = {
  active: "bg-emerald-500 text-white hover:bg-emerald-500/90",
  paused: "bg-amber-500 text-black hover:bg-amber-500/90",
  limited: "bg-orange-500 text-white hover:bg-orange-500/90",
  exceeded: "bg-red-600 text-white hover:bg-red-600/90",
  deactivated: "bg-zinc-500 text-white hover:bg-zinc-500/90",
};

export type StatusBadgeProps = {
  status: StatusValue | string;
};

export function StatusBadge({ status }: StatusBadgeProps) {
  const normalized = (status || "deactivated") as StatusValue;
  const className = statusClassMap[normalized] ?? statusClassMap.deactivated;
  const label = STATUS_LABELS[normalized] ?? status;

  return (
    <Badge className={className} variant="secondary">
      {label}
    </Badge>
  );
}
