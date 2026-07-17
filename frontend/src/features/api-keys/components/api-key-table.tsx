import { Ellipsis, KeyRound, Pencil, RefreshCw, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

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
import { formatCompactNumber, formatCurrency, formatTimeLong } from "@/utils/formatters";

function formatExpiry(value: string | null, neverLabel: string): string {
  if (!value) {
    return neverLabel;
  }
  const parsed = formatTimeLong(value);
  return `${parsed.date} ${parsed.time}`;
}

const LIMIT_TYPE_SHORT: Record<LimitType, string> = {
  total_tokens: "Tokens",
  input_tokens: "Input",
  output_tokens: "Output",
  cost_usd: "Cost",
  credits: "Credits",
};

function formatLimitSummary(limits: LimitRule[], t: ReturnType<typeof useTranslation>["t"]): string {
  if (limits.length === 0) return "-";
  return limits
    .map((l) => {
      const type = t(`apiKeys.limitTypes.${l.limitType}`, { defaultValue: LIMIT_TYPE_SHORT[l.limitType] });
      const isCost = l.limitType === "cost_usd";
      const isCredits = l.limitType === "credits";
      const current = isCost
        ? `$${(l.currentValue / 1_000_000).toFixed(2)}`
        : formatCompactNumber(l.currentValue);
      const max = isCost
        ? `$${(l.maxValue / 1_000_000).toFixed(2)}`
        : formatCompactNumber(l.maxValue);
      const suffix = isCost ? l.limitWindow : isCredits ? `${l.limitWindow}` : l.limitWindow;
      return `${type}: ${current}/${max} ${suffix}`;
    })
    .join(" | ");
}

function formatUsageSummary(
  requestCount: number,
  totalTokens: number,
  cachedInputTokens: number,
  totalCostUsd: number,
  t: ReturnType<typeof useTranslation>["t"],
): string {
  const total = formatCompactNumber(totalTokens);
  const cached = formatCompactNumber(cachedInputTokens);
  const requests = formatCompactNumber(requestCount);
  const cost = formatCurrency(totalCostUsd);
  return t("apiKeys.table.usageSummary", { total, cached, requests, cost });
}

function getUsageValue(apiKey: ApiKey, t: ReturnType<typeof useTranslation>["t"]): string {
  if (!apiKey.usageSummary) {
    return t("apiKeys.table.noUsage");
  }

  return formatUsageSummary(
    apiKey.usageSummary.requestCount,
    apiKey.usageSummary.totalTokens,
    apiKey.usageSummary.cachedInputTokens,
    apiKey.usageSummary.totalCostUsd,
    t,
  );
}

function getLimitValue(apiKey: ApiKey, t: ReturnType<typeof useTranslation>["t"]): string {
  if (apiKey.limits.length === 0) {
    return t("apiKeys.table.noLimit");
  }

  return formatLimitSummary(apiKey.limits, t);
}

export type ApiKeyTableProps = {
  keys: ApiKey[];
  busy: boolean;
  onEdit: (apiKey: ApiKey) => void;
  onDelete: (apiKey: ApiKey) => void;
  onRegenerate: (apiKey: ApiKey) => void;
};

export function ApiKeyTable({ keys, busy, onEdit, onDelete, onRegenerate }: ApiKeyTableProps) {
  const { t } = useTranslation();
  if (keys.length === 0) {
    return <EmptyState icon={KeyRound} title={t("apiKeys.table.empty")} />;
  }

  return (
    <div className="overflow-x-auto rounded-xl border">
    <Table className="table-fixed">
      <TableHeader>
        <TableRow>
          <TableHead className="w-[20%] min-w-[12rem] pl-4 text-[11px] uppercase tracking-wider text-muted-foreground/80">{t("apiKeys.table.name")}</TableHead>
          <TableHead className="w-[10%] min-w-[8rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">{t("apiKeys.table.prefix")}</TableHead>
          <TableHead className="w-[9%] min-w-[6.5rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">{t("apiKeys.table.models")}</TableHead>
          <TableHead className="w-[9%] min-w-[6.5rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">{t("apiKeys.table.traffic")}</TableHead>
          <TableHead className="w-[26%] min-w-[17rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">{t("apiKeys.table.usage")}</TableHead>
          <TableHead className="w-[14%] min-w-[12rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">{t("apiKeys.table.limit")}</TableHead>
          <TableHead className="w-[8%] min-w-[7rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">{t("apiKeys.table.expiry")}</TableHead>
          <TableHead className="w-[7%] min-w-[5.5rem] text-[11px] uppercase tracking-wider text-muted-foreground/80">{t("apiKeys.table.status")}</TableHead>
          <TableHead className="w-[6%] min-w-[4.5rem] pr-4 text-right text-[11px] uppercase tracking-wider text-muted-foreground/80">{t("apiKeys.table.actions")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {keys.map((apiKey) => {
          const models = apiKey.allowedModels?.join(", ") || t("common.options.all");
          const trafficClass = apiKey.trafficClass === "opportunistic" ? t("common.traffic.opportunistic") : t("common.traffic.foreground");

          return (
            <TableRow key={apiKey.id}>
              <TableCell className="pl-4 font-medium truncate">{apiKey.name}</TableCell>
              <TableCell className="truncate font-mono text-xs">{apiKey.keyPrefix}</TableCell>
              <TableCell className="truncate">{models}</TableCell>
              <TableCell className="truncate text-xs tabular-nums">{trafficClass}</TableCell>
              <TableCell className="text-xs tabular-nums leading-tight whitespace-normal">{getUsageValue(apiKey, t)}</TableCell>
              <TableCell className="text-xs tabular-nums leading-tight whitespace-normal">{getLimitValue(apiKey, t)}</TableCell>
              <TableCell className="truncate text-xs text-muted-foreground">{formatExpiry(apiKey.expiresAt, t("common.time.never"))}</TableCell>
              <TableCell>
                <Badge className={apiKey.isActive ? "bg-emerald-500 text-white" : "bg-zinc-500 text-white"}>
                  {apiKey.isActive ? t("common.states.active") : t("common.states.disabled")}
                </Badge>
              </TableCell>
              <TableCell className="pr-4 text-right">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button type="button" size="icon-sm" variant="ghost" disabled={busy}>
                      <Ellipsis className="size-4" />
                      <span className="sr-only">{t("apiKeys.table.actions")}</span>
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onClick={() => onEdit(apiKey)}>
                      <Pencil className="size-4" />
                      {t("common.actions.edit")}
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => onRegenerate(apiKey)}>
                      <RefreshCw className="size-4" />
                      {t("common.actions.regenerate")}
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem variant="destructive" onClick={() => onDelete(apiKey)}>
                      <Trash2 className="size-4" />
                      {t("common.actions.delete")}
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
