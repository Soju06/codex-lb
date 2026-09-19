import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { TeamGate } from "@/features/team/schemas";

const GATE_LABELS: Record<TeamGate, string> = {
  ok: "OK",
  near_cap: "Near cap",
  over_cap: "Over cap",
  suspended: "Suspended",
};

const GATE_CLASSES: Record<TeamGate, string> = {
  ok: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  near_cap: "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  over_cap: "border-destructive/30 bg-destructive/10 text-destructive",
  suspended: "border-border bg-muted text-muted-foreground",
};

export type TeamGateChipProps = {
  gate: TeamGate;
};

export function TeamGateChip({ gate }: TeamGateChipProps) {
  return (
    <Badge variant="outline" className={cn("tabular-nums", GATE_CLASSES[gate])} data-gate={gate}>
      {GATE_LABELS[gate]}
    </Badge>
  );
}
