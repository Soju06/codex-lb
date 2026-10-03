import { ArrowUpRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import type { ApiKey } from "@/features/api-keys/schemas";
import { API_LIST_COLUMNS, apiKeyStatus } from "@/features/apis/list-utils";
import { useDateDisplayFormatStore } from "@/hooks/use-date-format";
import { cn } from "@/lib/utils";
import { formatCompactNumber, formatDateTimeInline } from "@/utils/formatters";
import { ApiKeyQuotaBars } from "./api-key-quota-bars";

export function ApiListOverviewRow({
  apiKey,
  selected,
  onSelect,
}: {
  apiKey: ApiKey;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const { t } = useTranslation();
  const dateFormat = useDateDisplayFormatStore((s) => s.dateDisplayFormat);
  const status = apiKeyStatus(apiKey);
  const expiry = apiKey.expiresAt
    ? formatDateTimeInline(apiKey.expiresAt, dateFormat)
    : t("common.time.never");
  const lastUsed = apiKey.lastUsedAt
    ? formatDateTimeInline(apiKey.lastUsedAt, dateFormat)
    : "—";
  return (
    <button
      type="button"
      data-testid="api-list-overview-row"
      aria-haspopup="dialog"
      aria-label={t("apis.list.detailsFor", { name: apiKey.name })}
      onClick={() => onSelect(apiKey.id)}
      className={cn(
        "grid w-full min-w-0 grid-cols-2 items-center gap-x-3 gap-y-2 rounded-lg border bg-card px-4 py-2 text-left hover:border-primary/30 hover:bg-muted/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        API_LIST_COLUMNS,
        selected && "border-primary/30 bg-primary/[0.03]",
      )}
    >
      <div className="col-span-2 min-w-0 lg:col-span-1">
        <div className="flex items-center gap-2">
          <span
            className="min-w-0 flex-1 truncate text-sm font-semibold"
            title={apiKey.name}
          >
            {apiKey.name}
          </span>
          <ArrowUpRight
            className="size-3.5 shrink-0 text-muted-foreground"
            aria-hidden
          />
        </div>
        <span
          className="block truncate font-mono text-[11px] text-muted-foreground"
          title={apiKey.keyPrefix}
        >
          {apiKey.keyPrefix}
        </span>
      </div>
      <div className="min-w-0">
        <Badge
          className={cn(
            "text-[10px]",
            status === "active"
              ? "bg-emerald-500 text-white"
              : "bg-zinc-500 text-white",
          )}
        >
          {t(`common.states.${status}`)}
        </Badge>
      </div>
      <div className="min-w-0 text-xs tabular-nums">
        <span className="mr-1 text-muted-foreground lg:hidden">
          {t("apis.list.column.requests")}:
        </span>
        {apiKey.usageSummary
          ? formatCompactNumber(apiKey.usageSummary.requestCount)
          : "—"}
      </div>
      <div className="min-w-0 truncate text-[11px]" title={expiry}>
        <span className="block text-muted-foreground lg:hidden">
          {t("apis.list.column.expiry")}
        </span>
        {expiry}
      </div>
      <div className="min-w-0 truncate text-[11px]" title={lastUsed}>
        <span className="block text-muted-foreground lg:hidden">
          {t("apis.list.column.lastUsed")}
        </span>
        {lastUsed}
      </div>
      <div className="col-span-2 min-w-0 lg:col-span-1">
        <ApiKeyQuotaBars apiKey={apiKey} />
      </div>
    </button>
  );
}
