import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import type { ApiKey } from "@/features/api-keys/schemas";

export type ApiListItemProps = {
  apiKey: ApiKey;
  selected: boolean;
  onSelect: (keyId: string) => void;
};

function formatLimitPercent(apiKey: ApiKey): number | null {
  if (apiKey.limits.length === 0) return null;
  let maxPercent = 0;
  for (const limit of apiKey.limits) {
    if (limit.maxValue > 0) {
      const pct = (limit.currentValue / limit.maxValue) * 100;
      if (pct > maxPercent) maxPercent = pct;
    }
  }
  return maxPercent;
}

function limitBarColor(percent: number): string {
  if (percent >= 90) return "bg-red-500";
  if (percent >= 70) return "bg-orange-500";
  if (percent >= 40) return "bg-amber-500";
  return "bg-emerald-500";
}

function MiniUsageBar({ percent }: { percent: number | null }) {
  if (percent === null) {
    return <div className="h-1 flex-1 overflow-hidden rounded-full bg-muted" />;
  }
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div className="h-1 flex-1 overflow-hidden rounded-full bg-muted">
      <div
        className={cn("h-full rounded-full", limitBarColor(clamped))}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

function isExpired(apiKey: ApiKey): boolean {
  if (!apiKey.expiresAt) return false;
  return new Date(apiKey.expiresAt).getTime() < Date.now();
}

export function ApiListItem({ apiKey, selected, onSelect }: ApiListItemProps) {
  const limitPct = formatLimitPercent(apiKey);
  const expired = isExpired(apiKey);

  return (
    <button
      type="button"
      onClick={() => onSelect(apiKey.id)}
      className={cn(
        "w-full rounded-lg px-3 py-2.5 text-left transition-colors",
        selected
          ? "bg-primary/8 ring-1 ring-primary/25"
          : "hover:bg-muted/50",
      )}
    >
      <div className="flex items-center gap-2.5">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{apiKey.name}</p>
        </div>
        <Badge
          className={cn(
            !apiKey.isActive || expired
              ? "bg-zinc-500 text-white"
              : "bg-emerald-500 text-white",
          )}
        >
          {!apiKey.isActive ? "Disabled" : expired ? "Expired" : "Active"}
        </Badge>
      </div>
      <div className="mt-1.5">
        <MiniUsageBar percent={limitPct} />
      </div>
    </button>
  );
}
