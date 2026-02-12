import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import type { DashboardStat } from "@/features/dashboard/utils";

export type StatsGridProps = {
  stats: DashboardStat[];
};

export function StatsGrid({ stats }: StatsGridProps) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {stats.map((stat) => (
        <Card key={stat.label} className="gap-3 py-4">
          <CardHeader className="px-4 pb-0">
            <CardTitle className="text-xs font-medium text-muted-foreground">{stat.label}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 px-4">
            <p className="text-2xl font-semibold tracking-tight">{stat.value}</p>
            {stat.meta ? <p className="text-xs text-muted-foreground">{stat.meta}</p> : null}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
