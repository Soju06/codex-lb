import { Ellipsis, KeyRound, Pencil, RefreshCw, Trash2 } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { ApiKey, LimitRule, LimitType } from "@/features/api-keys/schemas";
import { formatCompactNumber, formatTimeLong } from "@/utils/formatters";

function formatExpiry(value: string | null): string {
  if (!value) {
    return "Never";
  }
  const parsed = formatTimeLong(value);
  return `${parsed.date} ${parsed.time}`;
}

const LIMIT_TYPE_SHORT: Record<LimitType, string> = {
  total_tokens: "Tokens",
  input_tokens: "Input",
  output_tokens: "Output",
  cost_usd: "Cost",
};

function formatLimitSummary(limits: LimitRule[]): string {
  if (limits.length === 0) return "-";
  return limits
    .map((l) => {
      const type = LIMIT_TYPE_SHORT[l.limitType];
      const isCost = l.limitType === "cost_usd";
      const current = isCost
        ? `$${(l.currentValue / 1_000_000).toFixed(2)}`
        : formatCompactNumber(l.currentValue);
      const max = isCost
        ? `$${(l.maxValue / 1_000_000).toFixed(2)}`
        : formatCompactNumber(l.maxValue);
      return `${type}: ${current}/${max} ${l.limitWindow}`;
    })
    .join(" | ");
}

function formatUsageSummary(
  requestCount: number,
  totalTokens: number,
  cachedInputTokens: number,
): string {
  const total = formatCompactNumber(totalTokens);
  const cached = formatCompactNumber(cachedInputTokens);
  const requests = formatCompactNumber(requestCount);
  return `${total} tok | ${cached} cached | ${requests} req`;
}

export type ApiKeyTableProps = {
  keys: ApiKey[];
  busy: boolean;
  onEdit: (apiKey: ApiKey) => void;
  onDelete: (apiKey: ApiKey) => void;
  onRegenerate: (apiKey: ApiKey) => void;
};

export function ApiKeyTable({ keys, busy, onEdit, onDelete, onRegenerate }: ApiKeyTableProps) {
  if (keys.length === 0) {
    return <EmptyState icon={KeyRound} title="No API keys created yet" />;
  }

  return (
    <div className="overflow-x-auto rounded-xl border">
    <Table className="table-fixed">
      <TableHeader>
        <TableRow>
          <TableHead className="w-[22%] min-w-[14rem] pl-4 text-[11px] uppercase tracking-wider text-muted-foreground/80">Name</TableHead>
          <TableHead className="w-[12%] min-w-[9rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">Prefix</TableHead>
          <TableHead className="w-[14%] min-w-[9rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">Models</TableHead>
          <TableHead className="w-[30%] min-w-[18rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">Usage</TableHead>
          <TableHead className="w-[9%] min-w-[8rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">Expiry</TableHead>
          <TableHead className="w-[7%] min-w-[6rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">Status</TableHead>
          <TableHead className="w-[6%] min-w-[5rem] pr-4 text-right text-[11px] uppercase tracking-wider text-muted-foreground/80">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {keys.map((apiKey) => {
          const models = apiKey.allowedModels?.join(", ") || "All";
          const usageText = apiKey.limits.length > 0
            ? formatLimitSummary(apiKey.limits)
            : apiKey.usageSummary && apiKey.usageSummary.requestCount > 0
              ? formatUsageSummary(
                  apiKey.usageSummary.requestCount,
                  apiKey.usageSummary.totalTokens,
                  apiKey.usageSummary.cachedInputTokens,
                )
              : "No usage";

          return (
            <TableRow key={apiKey.id}>
              <TableCell className="pl-4 font-medium truncate">{apiKey.name}</TableCell>
              <TableCell className="truncate font-mono text-xs">{apiKey.keyPrefix}</TableCell>
              <TableCell className="truncate">{models}</TableCell>
              <TableCell className="text-xs tabular-nums leading-tight">{usageText}</TableCell>
              <TableCell className="truncate text-xs text-muted-foreground">{formatExpiry(apiKey.expiresAt)}</TableCell>
              <TableCell>
                <Badge className={apiKey.isActive ? "bg-emerald-500 text-white" : "bg-zinc-500 text-white"}>
                  {apiKey.isActive ? "Active" : "Disabled"}
                </Badge>
              </TableCell>
              <TableCell className="pr-4 text-right">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button type="button" size="icon-sm" variant="ghost" disabled={busy}>
                      <Ellipsis className="size-4" />
                      <span className="sr-only">Actions</span>
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onClick={() => onEdit(apiKey)}>
                      <Pencil className="size-4" />
                      Edit
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => onRegenerate(apiKey)}>
                      <RefreshCw className="size-4" />
                      Regenerate
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem variant="destructive" onClick={() => onDelete(apiKey)}>
                      <Trash2 className="size-4" />
                      Delete
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
    </div>
  );
}
