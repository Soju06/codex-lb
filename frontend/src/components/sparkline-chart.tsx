import { Area, AreaChart, ResponsiveContainer } from "recharts";

const CHART_MARGIN = { top: 2, right: 0, bottom: 2, left: 0 } as const;

export type SparklineChartProps = {
  data: { value: number }[];
  color: string;
  index: number;
};

export function SparklineChart({ data, color, index }: SparklineChartProps) {
  const gradientId = `sparkline-fill-${index}`;

  return (
    <ResponsiveContainer width="100%" height={40}>
      <AreaChart data={data} margin={CHART_MARGIN}>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.3} />
            <stop offset="100%" stopColor={color} stopOpacity={0.05} />
          </linearGradient>
        </defs>
        <Area
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={1.5}
          fill={`url(#${gradientId})`}
          isAnimationActive={true}
          animationDuration={500}
          animationBegin={index * 100}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
