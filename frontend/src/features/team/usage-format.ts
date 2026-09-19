import type { TeamMember, TeamUsageWindow, TeamWindow } from "@/features/team/schemas";
import { formatCompactNumber, formatCurrency } from "@/utils/formatters";

const WINDOW_CAP_FIELDS: Record<
  TeamWindow,
  { cost: keyof TeamMember; tokens: keyof TeamMember }
> = {
  day: { cost: "costCapDayUsd", tokens: "tokenCapDay" },
  week: { cost: "costCapWeekUsd", tokens: "tokenCapWeek" },
  month: { cost: "costCapMonthUsd", tokens: "tokenCapMonth" },
};

export function formatUsageAgainstCap(member: TeamMember, window: TeamWindow): string {
  const usage: TeamUsageWindow = member.usage[window];
  const fields = WINDOW_CAP_FIELDS[window];
  const costCap = member[fields.cost] as number | null;
  const tokenCap = member[fields.tokens] as number | null;

  const parts: string[] = [];
  if (costCap !== null) {
    parts.push(`${formatCurrency(usage.costUsd)} / ${formatCurrency(costCap)}`);
  } else {
    parts.push(`${formatCurrency(usage.costUsd)} / no cap`);
  }
  if (tokenCap !== null) {
    parts.push(`${formatCompactNumber(usage.tokens)} / ${formatCompactNumber(tokenCap)} tok`);
  }
  return parts.join(" · ");
}
