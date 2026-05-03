import { Medal } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  formatAccountPriorityLabel,
  normalizeAccountPriority,
  type AccountPriority,
} from "@/features/accounts/priority";

const PRIORITY_STYLES: Record<AccountPriority, string> = {
  gold: "border-amber-500/25 bg-amber-500/15 text-amber-700 dark:text-amber-300",
  silver: "border-slate-500/25 bg-slate-500/15 text-slate-700 dark:text-slate-300",
  bronze: "border-orange-500/25 bg-orange-500/15 text-orange-700 dark:text-orange-300",
};

export type AccountPriorityBadgeProps = {
  priority: string | null | undefined;
};

export function AccountPriorityBadge({ priority }: AccountPriorityBadgeProps) {
  const normalized = normalizeAccountPriority(priority);

  return (
    <Badge className={cn("gap-1.5", PRIORITY_STYLES[normalized])} variant="outline">
      <Medal className="h-3 w-3" aria-hidden />
      {formatAccountPriorityLabel(normalized)}
    </Badge>
  );
}
