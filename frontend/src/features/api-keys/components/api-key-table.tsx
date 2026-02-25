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

export type ApiKeyTableProps = {
  keys: ApiKey[];
  busy: boolean;
  showOwner?: boolean;
  ownerLabels?: Record<string, string>;
  onEdit: (apiKey: ApiKey) => void;
  onDelete: (apiKey: ApiKey) => void;
  onRegenerate: (apiKey: ApiKey) => void;
};

export function ApiKeyTable({
  keys,
  busy,
  showOwner = false,
  ownerLabels = {},
  onEdit,
  onDelete,
  onRegenerate,
}: ApiKeyTableProps) {
  if (keys.length === 0) {
    return <EmptyState icon={KeyRound} title="No API keys created yet" />;
  }

  return (
    <div className="overflow-x-auto rounded-xl border">
    <Table className="table-fixed">
      <TableHeader>
        <TableRow>
          {showOwner ? (
            <TableHead className="w-[10%] pl-4 text-[11px] uppercase tracking-wider text-muted-foreground/80">Owner</TableHead>
          ) : null}
          <TableHead className="w-[12%] pl-4 text-[11px] uppercase tracking-wider text-muted-foreground/80">Name</TableHead>
          <TableHead className="w-[10%] text-[11px] uppercase tracking-wider text-muted-foreground/80">Prefix</TableHead>
          <TableHead className="w-[14%] text-[11px] uppercase tracking-wider text-muted-foreground/80">Models</TableHead>
          <TableHead className="w-[24%] text-[11px] uppercase tracking-wider text-muted-foreground/80">Usage</TableHead>
          <TableHead className="w-[18%] text-[11px] uppercase tracking-wider text-muted-foreground/80">Expiry</TableHead>
          <TableHead className="w-[12%] text-[11px] uppercase tracking-wider text-muted-foreground/80">Status</TableHead>
          <TableHead className="w-[10%] pr-4 text-[11px] uppercase tracking-wider text-muted-foreground/80">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {keys.map((apiKey) => {
          const models = apiKey.allowedModels?.join(", ") || "All";
          const usageText = formatLimitSummary(apiKey.limits);
          const ownerLabel = apiKey.ownerUserId ? ownerLabels[apiKey.ownerUserId] ?? apiKey.ownerUserId : "—";

          return (
            <TableRow key={apiKey.id}>
              {showOwner ? (
                <TableCell className="pl-4 text-xs text-muted-foreground">{ownerLabel}</TableCell>
              ) : null}
              <TableCell className="pl-4 font-medium">{apiKey.name}</TableCell>
              <TableCell className="font-mono text-xs">{apiKey.keyPrefix}</TableCell>
              <TableCell className="max-w-[14rem] truncate">{models}</TableCell>
              <TableCell className="truncate text-xs tabular-nums">{usageText}</TableCell>
              <TableCell className="text-xs text-muted-foreground">{formatExpiry(apiKey.expiresAt)}</TableCell>
              <TableCell>
                <Badge className={apiKey.isActive ? "bg-emerald-500 text-white" : "bg-zinc-500 text-white"}>
                  {apiKey.isActive ? "Active" : "Disabled"}
                </Badge>
              </TableCell>
              <TableCell className="pr-4">
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
