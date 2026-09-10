import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { TooltipContentProps } from "recharts";

import type { KeyDashboardGroup } from "@/features/key-dashboard/schemas";
import { formatCompactNumber } from "@/utils/formatters";

type Metric = "tokens" | "cost";
type Member = KeyDashboardGroup["members"][number];
type DailyChartRow = { date: string; values: number[] };

const COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#06b6d4", "#f97316", "#64748b"];
const memberLabel = (member: Member) => `${member.name} · ${member.keyPrefix}`;

function DailyTooltip({ active, payload, label, formatValue }: Partial<TooltipContentProps<number, string>> & {
  formatValue: (value: number) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="max-h-64 max-w-[min(80vw,28rem)] overflow-auto rounded-xl border bg-popover p-3 text-xs shadow-md">
      <p className="mb-2 font-medium text-muted-foreground">{label} (UTC)</p>
      <div className="space-y-2">
        {payload.map((entry) => (
          <div key={String(entry.dataKey)} className="flex items-start gap-2">
            <span className="mt-1 size-2 shrink-0 rounded-full" style={{ backgroundColor: entry.color }} />
            <span className="min-w-0 break-words text-popover-foreground">{entry.name}</span>
            <span className="ml-auto shrink-0 pl-2 font-semibold tabular-nums text-popover-foreground">
              {formatValue(Number(entry.value))}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function KeyGroupDailyChart({ members }: { members: Member[] }) {
  const { t, i18n } = useTranslation();
  const [metric, setMetric] = useState<Metric>("tokens");
  const [hiddenMembers, setHiddenMembers] = useState<Set<number>>(() => new Set());
  const [showTable, setShowTable] = useState(false);
  const number = new Intl.NumberFormat(i18n.language);
  const currency = new Intl.NumberFormat(i18n.language, {
    style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 6,
  });
  const formatValue = (value: number) => metric === "tokens" ? number.format(value) : currency.format(value);
  const metricLabel = t(`keyDashboard.group.daily.${metric}`);
  const visible = members.map((member, index) => ({ member, index })).filter(({ index }) => !hiddenMembers.has(index));
  const chartData = useMemo<DailyChartRow[]>(() => (members[0]?.dailyUsage ?? []).map((day, dayIndex) => ({
    date: day.date,
    values: members.map((member) => metric === "tokens"
      ? member.dailyUsage[dayIndex].totalTokens
      : member.dailyUsage[dayIndex].totalCostUsd),
  })), [members, metric]);

  return (
    <section className="min-w-0 space-y-4 rounded-xl border bg-card p-4 sm:p-5" aria-label={t("keyDashboard.group.daily.title")}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-semibold">{t("keyDashboard.group.daily.title")}</h3>
          <p className="mt-1 max-w-xl text-xs text-muted-foreground">{t("keyDashboard.group.daily.description")}</p>
        </div>
        <fieldset className="flex shrink-0 gap-1 rounded-lg border bg-muted/40 p-1">
          <legend className="sr-only">{t("keyDashboard.group.daily.metric")}</legend>
          {(["tokens", "cost"] as const).map((value) => (
            <label key={value} className="cursor-pointer">
              <input type="radio" name="group-daily-metric" value={value} checked={metric === value}
                onChange={() => setMetric(value)} className="peer sr-only" />
              <span className="block rounded-md px-3 py-1.5 text-xs font-medium text-muted-foreground peer-checked:bg-background peer-checked:text-foreground peer-checked:shadow-sm peer-focus-visible:ring-2 peer-focus-visible:ring-ring">
                {t(`keyDashboard.group.daily.${value}`)}
              </span>
            </label>
          ))}
        </fieldset>
      </div>
      <p className="text-xs font-medium text-muted-foreground">{metricLabel}</p>
      <div className="h-64 min-w-0 sm:h-72">
        {visible.length > 0 && chartData.length > 0 ? (
          <ResponsiveContainer width="100%" height="100%" minWidth={0}>
            <LineChart data={chartData} margin={{ top: 8, right: 12, left: 0, bottom: 0 }} accessibilityLayer>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
              <XAxis dataKey="date" tickFormatter={(value: string) => value.slice(5)}
                tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false}
                minTickGap={28} interval="preserveStartEnd" />
              <YAxis width={56} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                axisLine={false} tickLine={false} allowDecimals={metric === "cost"} domain={[0, "auto"]}
                tickFormatter={(value: number) => metric === "tokens" ? formatCompactNumber(value) : `$${formatCompactNumber(value)}`} />
              <Tooltip content={<DailyTooltip formatValue={formatValue} />} />
              {visible.map(({ member, index }) => (
                <Line key={index} type="linear" dataKey={`values[${index}]`} name={memberLabel(member)}
                  stroke={COLORS[index % COLORS.length]} strokeWidth={2}
                  strokeDasharray={index >= COLORS.length ? `${2 + Math.floor(index / COLORS.length) * 2} 3` : undefined}
                  dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-full items-center justify-center text-center text-sm text-muted-foreground">
            {t("keyDashboard.group.daily.noSelection")}
          </div>
        )}
      </div>
      <fieldset className="flex max-h-36 flex-wrap gap-x-4 gap-y-2 overflow-y-auto">
        <legend className="mb-2 text-xs text-muted-foreground">{t("keyDashboard.group.daily.keys")}</legend>
        {members.map((member, index) => (
          <label key={index} className="flex min-w-0 max-w-full cursor-pointer items-center gap-2 text-xs">
            <input type="checkbox" checked={!hiddenMembers.has(index)} className="size-3.5 shrink-0 accent-primary"
              onChange={() => setHiddenMembers((previous) => {
                const next = new Set(previous);
                if (next.has(index)) next.delete(index); else next.add(index);
                return next;
              })} />
            <span className="h-0.5 w-4 shrink-0" style={{ backgroundColor: COLORS[index % COLORS.length] }} aria-hidden="true" />
            <span className="break-words">{memberLabel(member)}</span>
          </label>
        ))}
      </fieldset>
      <details className="border-t pt-3" onToggle={(event) => setShowTable(event.currentTarget.open)}>
        <summary className="cursor-pointer text-xs font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring">
          {t("keyDashboard.group.daily.table")}
        </summary>
        {showTable && visible.length > 0 ? (
          <div className="mt-3 max-h-80 overflow-auto rounded-lg border">
            <table className="w-full text-xs">
              <caption className="sr-only">{t("keyDashboard.group.daily.tableCaption", { metric: metricLabel })}</caption>
              <thead className="sticky top-0 bg-muted">
                <tr>
                  <th scope="col" className="whitespace-nowrap px-3 py-2 text-left">{t("keyDashboard.group.daily.date")}</th>
                  {visible.map(({ member, index }) => (
                    <th key={index} scope="col" className="min-w-40 px-3 py-2 text-right font-medium">{memberLabel(member)}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y">
                {chartData.map((day) => (
                  <tr key={day.date}>
                    <th scope="row" className="whitespace-nowrap px-3 py-2 text-left font-normal">{day.date}</th>
                    {visible.map(({ index }) => (
                      <td key={index} className="px-3 py-2 text-right tabular-nums">{formatValue(day.values[index])}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </details>
    </section>
  );
}
