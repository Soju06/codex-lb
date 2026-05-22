import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { MiniQuotaBar } from "@/components/mini-quota-bar";
import type { ApiKey } from "@/features/api-keys/schemas";
import { formatPercentNullable } from "@/utils/formatters";

export type ApiListItemProps = {
  apiKey: ApiKey;
  selected: boolean;
  onSelect: (keyId: string) => void;
};

function isExpired(apiKey: ApiKey): boolean {
  if (!apiKey.expiresAt) return false;
  return new Date(apiKey.expiresAt).getTime() < Date.now();
}

export function ApiListItem({ apiKey, selected, onSelect }: ApiListItemProps) {
  const expired = isExpired(apiKey);
  const primary = apiKey.pooledRemainingPercentPrimary ?? null;
  const secondary = apiKey.pooledRemainingPercentSecondary ?? null;
  const hasPrimary = apiKey.pooledCapacityCreditsPrimary > 0 && primary !== null;
  const hasSecondary = secondary !== null;
  const visibleRows = Number(hasPrimary) + Number(hasSecondary);

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
      {visibleRows > 0 ? (
        <div className={cn("mt-2 grid gap-2", visibleRows > 1 ? "grid-cols-2" : "grid-cols-1")}>
          {hasPrimary ? (
            <div className="space-y-1">
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-muted-foreground">Pooled 5h</span>
                <span className="tabular-nums font-medium">{formatPercentNullable(primary)}</span>
              </div>
              <MiniQuotaBar percent={primary} testId="pooled-quota-track-5h" />
            </div>
          ) : null}
          {hasSecondary ? (
            <div className="space-y-1">
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-muted-foreground">Pooled Weekly</span>
                <span className="tabular-nums font-medium">{formatPercentNullable(secondary)}</span>
              </div>
              <MiniQuotaBar percent={secondary} testId="pooled-quota-track-weekly" />
            </div>
          ) : null}
        </div>
      ) : null}
    </button>
  );
}
