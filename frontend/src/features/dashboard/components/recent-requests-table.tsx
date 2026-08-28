import { Inbox } from "lucide-react";
import {
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import { useTranslation } from "react-i18next";

import { isEmailLabel } from "@/components/blur-email";
import { CopyButton } from "@/components/copy-button";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PaginationControls } from "@/features/dashboard/components/filters/pagination-controls";
import { RequestArchivePanel } from "@/features/conversation-archive/components/request-archive-panel";
import {
  ALL_REQUEST_LOG_COLUMNS,
  MAX_REQUEST_LOG_COLUMN_WIDTH,
  MIN_REQUEST_LOG_COLUMN_WIDTH,
  REQUEST_LOG_COLUMN_DEFAULT_WIDTHS,
  REQUEST_LOG_COLUMN_WIDTH_STEP,
  clampRequestLogColumnWidth,
  type RequestLogColumnId,
  type RequestLogColumnWidths,
} from "@/features/dashboard/request-log-columns";
import type { AccountSummary, RequestLog } from "@/features/dashboard/schemas";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { useDateDisplayFormatStore } from "@/hooks/use-date-format";
import { cn } from "@/lib/utils";
import { REQUEST_STATUS_LABELS } from "@/utils/constants";
import {
  formatDateTimeInline,
  formatDateTimeLines,
  formatCompactNumber,
  formatCurrency,
  formatModelLabel,
  formatElapsed,
  formatSlug,
} from "@/utils/formatters";

const STATUS_CLASS_MAP: Record<string, string> = {
  ok: "bg-emerald-500/15 text-emerald-700 border-emerald-500/20 hover:bg-emerald-500/20 dark:text-emerald-400",
  cancelled: "bg-sky-500/15 text-sky-700 border-sky-500/20 hover:bg-sky-500/20 dark:text-sky-400",
  rate_limit: "bg-orange-500/15 text-orange-700 border-orange-500/20 hover:bg-orange-500/20 dark:text-orange-400",
  quota: "bg-red-500/15 text-red-700 border-red-500/20 hover:bg-red-500/20 dark:text-red-400",
  error: "bg-zinc-500/15 text-zinc-700 border-zinc-500/20 hover:bg-zinc-500/20 dark:text-zinc-400",
};

const TRANSPORT_LABELS: Record<string, string> = {
  auto: "Auto",
  http: "HTTP",
  websocket: "WS",
  automation: "Automation",
};

const TRANSPORT_CLASS_MAP: Record<string, string> = {
  auto: "bg-purple-500/10 text-purple-700 border-purple-500/20 hover:bg-purple-500/15 dark:text-purple-300",
  http: "bg-slate-500/10 text-slate-700 border-slate-500/20 hover:bg-slate-500/15 dark:text-slate-300",
  websocket: "bg-sky-500/15 text-sky-700 border-sky-500/20 hover:bg-sky-500/20 dark:text-sky-300",
  automation:
    "bg-indigo-500/15 text-indigo-700 border-indigo-500/20 hover:bg-indigo-500/20 dark:text-indigo-300",
};

const PLAN_CLASS_MAP: Record<string, string> = {
  free: "bg-zinc-500/10 text-zinc-700 border-zinc-500/20 hover:bg-zinc-500/15 dark:text-zinc-300",
  plus: "bg-emerald-500/15 text-emerald-700 border-emerald-500/20 hover:bg-emerald-500/20 dark:text-emerald-400",
  team: "bg-sky-500/15 text-sky-700 border-sky-500/20 hover:bg-sky-500/20 dark:text-sky-300",
  pro: "bg-violet-500/15 text-violet-700 border-violet-500/20 hover:bg-violet-500/20 dark:text-violet-300",
};

const REQUEST_KIND_LABELS: Record<string, string> = {
  normal: "Normal",
  warmup: "Warmup",
  limit_warmup: "Warmup",
};

export type RecentRequestsTableProps = {
  requests: RequestLog[];
  accounts: AccountSummary[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  filtersApplied?: boolean;
  visibleColumns?: readonly RequestLogColumnId[];
  columnWidths?: RequestLogColumnWidths;
  onColumnWidthChange?: (column: RequestLogColumnId, width: number) => void;
  onLimitChange: (limit: number) => void;
  onOffsetChange: (offset: number) => void;
  onConversationClick?: (conversationId: string) => void;
};

type RequestLogTableHeadProps = {
  column: RequestLogColumnId;
  label: string;
  resizeLabel: string;
  className?: string;
  width?: number;
  onWidthChange?: (column: RequestLogColumnId, width: number) => void;
};

function RequestLogTableHead({
  column,
  label,
  resizeLabel,
  className,
  width,
  onWidthChange,
}: RequestLogTableHeadProps) {
  const resizeState = useRef<{
    pointerId: number;
    startX: number;
    startWidth: number;
  } | null>(null);
  const resolvedWidth = clampRequestLogColumnWidth(
    width ?? REQUEST_LOG_COLUMN_DEFAULT_WIDTHS[column],
  );

  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (!onWidthChange) {
      return;
    }

    event.preventDefault();
    const measuredWidth = event.currentTarget.parentElement?.getBoundingClientRect().width;
    resizeState.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth: measuredWidth && measuredWidth > 0 ? measuredWidth : resolvedWidth,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const handlePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const state = resizeState.current;
    if (!state || state.pointerId !== event.pointerId || !onWidthChange) {
      return;
    }

    onWidthChange(
      column,
      clampRequestLogColumnWidth(state.startWidth + event.clientX - state.startX),
    );
  };

  const handlePointerEnd = (event: PointerEvent<HTMLDivElement>) => {
    if (resizeState.current?.pointerId !== event.pointerId) {
      return;
    }

    resizeState.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!onWidthChange || (event.key !== "ArrowLeft" && event.key !== "ArrowRight")) {
      return;
    }

    event.preventDefault();
    const direction = event.key === "ArrowLeft" ? -1 : 1;
    onWidthChange(
      column,
      clampRequestLogColumnWidth(
        resolvedWidth + direction * REQUEST_LOG_COLUMN_WIDTH_STEP,
      ),
    );
  };

  return (
    <TableHead
      aria-label={label}
      className={cn(
        "relative text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80",
        className,
      )}
      style={onWidthChange ? { width: resolvedWidth } : undefined}
    >
      {label}
      {onWidthChange ? (
        <div
          role="separator"
          aria-label={resizeLabel}
          aria-orientation="vertical"
          aria-valuemin={MIN_REQUEST_LOG_COLUMN_WIDTH}
          aria-valuemax={MAX_REQUEST_LOG_COLUMN_WIDTH}
          aria-valuenow={resolvedWidth}
          tabIndex={0}
          className="group absolute -right-1 top-0 z-10 h-full w-2 cursor-col-resize touch-none select-none outline-none"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerEnd}
          onPointerCancel={handlePointerEnd}
          onLostPointerCapture={() => {
            resizeState.current = null;
          }}
          onKeyDown={handleKeyDown}
        >
          <span
            aria-hidden="true"
            className="absolute left-1/2 top-1 h-[calc(100%-0.5rem)] w-px -translate-x-1/2 bg-border transition-colors group-hover:bg-primary group-focus-visible:bg-primary"
          />
        </div>
      ) : null}
    </TableHead>
  );
}

function formatRequestCostSummary(request: RequestLog | null, t: ReturnType<typeof useTranslation>["t"]): string | null {
  if (!request || request.status !== "ok") {
    return null;
  }

  const totalUsd = request.costBreakdown?.totalUsd ?? request.costUsd;
  const segments: string[] = [];
  const cachedInputTokens = request.cachedInputTokens ?? 0;
  const nonCachedInputTokens =
    request.inputTokens == null ? null : Math.max(0, request.inputTokens - cachedInputTokens);

  if (nonCachedInputTokens != null && request.costBreakdown?.inputUsd != null) {
    segments.push(
      t("dashboard.requestDetails.costSegment", {
        count: formatCompactNumber(nonCachedInputTokens),
        label: t("common.units.input"),
        cost: formatCurrency(request.costBreakdown.inputUsd),
      }),
    );
  }

  if (request.cachedInputTokens != null && request.costBreakdown?.cachedInputUsd != null) {
    segments.push(
      t("dashboard.requestDetails.costSegment", {
        count: formatCompactNumber(request.cachedInputTokens),
        label: t("common.units.cached"),
        cost: formatCurrency(request.costBreakdown.cachedInputUsd),
      }),
    );
  }

  if (request.outputTokens != null && request.costBreakdown?.outputUsd != null) {
    segments.push(
      t("dashboard.requestDetails.costSegment", {
        count: formatCompactNumber(request.outputTokens),
        label: t("common.units.output"),
        cost: formatCurrency(request.costBreakdown.outputUsd),
      }),
    );
  }

  if (segments.length === 0) {
    return null;
  }

  if (totalUsd == null) {
    return segments.join(" + ");
  }

  return `${formatCurrency(totalUsd)} = ${segments.join(" + ")}`;
}

function formatGenerationSpeed(request: RequestLog): string | null {
  if (request.outputTokensRaw == null || request.latencyMs == null || request.latencyFirstTokenMs == null) {
    return null;
  }

  const outputCount = request.outputTokensRaw - (request.reasoningTokens ?? 0);
  const generationMs = request.latencyMs - request.latencyFirstTokenMs;
  if (outputCount <= 0 || generationMs <= 0) {
    return null;
  }

  return (outputCount / (generationMs / 1000)).toFixed(1);
}

function formatCompactElapsed(ms: number | null | undefined): string | null {
  if (ms == null) {
    return null;
  }
  if (ms < 1000) {
    return `${ms}ms`;
  }
  return `${(ms / 1000).toFixed(1)}s`;
}

export function RecentRequestsTable({
  requests,
  accounts,
  total,
  limit,
  offset,
  hasMore,
  filtersApplied = false,
  visibleColumns: configuredVisibleColumns,
  columnWidths,
  onColumnWidthChange,
  onLimitChange,
  onOffsetChange,
  onConversationClick,
}: RecentRequestsTableProps) {
  const { t } = useTranslation();
  const [selectedRequest, setSelectedRequest] = useState<RequestLog | null>(null);
  const blurred = usePrivacyStore((s) => s.blurred);
  const isAdmin = useAuthStore((state) => state.role === "admin");
  const dateDisplayFormat = useDateDisplayFormatStore((state) => state.dateDisplayFormat);
  const selectedRequestCostSummary = formatRequestCostSummary(selectedRequest, t);
  const visibleColumns = configuredVisibleColumns ?? ALL_REQUEST_LOG_COLUMNS;
  const visibleColumnSet = useMemo(() => new Set(visibleColumns), [visibleColumns]);
  const hasConfiguredLayout =
    configuredVisibleColumns !== undefined ||
    columnWidths !== undefined ||
    onColumnWidthChange !== undefined;
  const tableWidth = hasConfiguredLayout
    ? visibleColumns.reduce(
        (totalWidth, column) =>
          totalWidth +
          clampRequestLogColumnWidth(
            columnWidths?.[column] ?? REQUEST_LOG_COLUMN_DEFAULT_WIDTHS[column],
          ),
        0,
      )
    : undefined;
  const isColumnVisible = (column: RequestLogColumnId) => visibleColumnSet.has(column);
  const resizeLabel = (label: string) =>
    t("dashboard.requests.resizeColumn", { column: label });

  const accountLabelMap = useMemo(() => {
    const index = new Map<string, string>();
    for (const account of accounts) {
      index.set(account.accountId, account.displayName || account.email || account.accountId);
    }
    return index;
  }, [accounts]);

  /** Account IDs whose label is an email. */
  const emailLabelIds = useMemo(() => {
    const ids = new Set<string>();
    for (const account of accounts) {
      const label = account.displayName || account.email;
      if (isEmailLabel(label, account.email)) {
        ids.add(account.accountId);
      }
    }
    return ids;
  }, [accounts]);

  if (requests.length === 0) {
    const emptyFromExistingLogs = filtersApplied || total > 0;
    return (
      <EmptyState
        icon={Inbox}
        title={
          emptyFromExistingLogs
            ? t("dashboard.requests.emptyFilteredTitle")
            : t("dashboard.requests.emptyTitle")
        }
        description={
          emptyFromExistingLogs
            ? t("dashboard.requests.emptyFilteredDescription")
            : t("dashboard.requests.emptyDescription")
        }
      />
    );
  }

  return (
    <div className="space-y-3">
    <div className="rounded-xl border bg-card">
      <div className="relative overflow-x-auto">
        <Table
          className="w-full table-fixed"
          style={tableWidth === undefined ? undefined : { width: tableWidth, minWidth: tableWidth }}
        >
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              {isColumnVisible("time") ? <RequestLogTableHead column="time" label={t("dashboard.requests.columns.time")} resizeLabel={resizeLabel(t("dashboard.requests.columns.time"))} className="pl-4" width={columnWidths?.time} onWidthChange={onColumnWidthChange} /> : null}
              {isColumnVisible("account") ? <RequestLogTableHead column="account" label={t("dashboard.requests.columns.account")} resizeLabel={resizeLabel(t("dashboard.requests.columns.account"))} width={columnWidths?.account} onWidthChange={onColumnWidthChange} /> : null}
              {isColumnVisible("plan") ? <RequestLogTableHead column="plan" label={t("dashboard.requests.columns.plan")} resizeLabel={resizeLabel(t("dashboard.requests.columns.plan"))} width={columnWidths?.plan} onWidthChange={onColumnWidthChange} /> : null}
              {isColumnVisible("apiKey") ? <RequestLogTableHead column="apiKey" label={t("dashboard.requests.columns.apiKey")} resizeLabel={resizeLabel(t("dashboard.requests.columns.apiKey"))} width={columnWidths?.apiKey} onWidthChange={onColumnWidthChange} /> : null}
              {isColumnVisible("model") ? <RequestLogTableHead column="model" label={t("dashboard.requests.columns.model")} resizeLabel={resizeLabel(t("dashboard.requests.columns.model"))} width={columnWidths?.model} onWidthChange={onColumnWidthChange} /> : null}
              {isColumnVisible("transport") ? <RequestLogTableHead column="transport" label={t("dashboard.requests.columns.transport")} resizeLabel={resizeLabel(t("dashboard.requests.columns.transport"))} className="pr-3" width={columnWidths?.transport} onWidthChange={onColumnWidthChange} /> : null}
              {isColumnVisible("status") ? <RequestLogTableHead column="status" label={t("dashboard.requests.columns.status")} resizeLabel={resizeLabel(t("dashboard.requests.columns.status"))} className="pl-3" width={columnWidths?.status} onWidthChange={onColumnWidthChange} /> : null}
              {isColumnVisible("ttft") ? <RequestLogTableHead column="ttft" label={t("dashboard.requests.columns.ttft")} resizeLabel={resizeLabel(t("dashboard.requests.columns.ttft"))} className="text-right" width={columnWidths?.ttft} onWidthChange={onColumnWidthChange} /> : null}
              {isColumnVisible("tps") ? <RequestLogTableHead column="tps" label={t("dashboard.requests.columns.tps")} resizeLabel={resizeLabel(t("dashboard.requests.columns.tps"))} className="text-right" width={columnWidths?.tps} onWidthChange={onColumnWidthChange} /> : null}
              {isColumnVisible("tokens") ? <RequestLogTableHead column="tokens" label={t("dashboard.requests.columns.tokens")} resizeLabel={resizeLabel(t("dashboard.requests.columns.tokens"))} className="text-right" width={columnWidths?.tokens} onWidthChange={onColumnWidthChange} /> : null}
              {isColumnVisible("cost") ? <RequestLogTableHead column="cost" label={t("dashboard.requests.columns.cost")} resizeLabel={resizeLabel(t("dashboard.requests.columns.cost"))} className="text-right" width={columnWidths?.cost} onWidthChange={onColumnWidthChange} /> : null}
              {isColumnVisible("details") ? <RequestLogTableHead column="details" label={t("dashboard.requests.columns.details")} resizeLabel={resizeLabel(t("dashboard.requests.columns.details"))} className="pr-4" width={columnWidths?.details} onWidthChange={onColumnWidthChange} /> : null}
            </TableRow>
          </TableHeader>
          <TableBody>
            {requests.map((request) => {
              const time = formatDateTimeLines(request.requestedAt, dateDisplayFormat);
              const accountLabel = request.accountId ? (accountLabelMap.get(request.accountId) ?? request.accountId) : t("dashboard.requests.unassigned");
              const isEmailLabel = !!(request.accountId && emailLabelIds.has(request.accountId));
              const errorPreview = request.errorMessage || request.errorCode || "-";
              const hasError = !!(request.errorCode || request.errorMessage);
              const visibleServiceTier = request.actualServiceTier ?? request.serviceTier;
              const showRequestedTier =
                !!request.requestedServiceTier && request.requestedServiceTier !== visibleServiceTier;
              const planType = request.planType?.trim().toLowerCase() || null;
              const planLabel = planType ? formatSlug(planType) : "--";
              const upstreamTransport = request.upstreamTransport;
              const generationSpeed = formatGenerationSpeed(request);

              return (
                <TableRow key={request.requestId}>
                  {isColumnVisible("time") ? <TableCell className="pl-4 align-top">
                    <div className="leading-tight">
                      <div className="text-sm font-medium">{time.primary}</div>
                      <div className="text-xs text-muted-foreground">{time.secondary}</div>
                    </div>
                  </TableCell> : null}
                  {isColumnVisible("account") ? <TableCell className="truncate align-top text-sm">
                    {isEmailLabel && blurred ? (
                      <span className="privacy-blur">{accountLabel}</span>
                    ) : (
                      accountLabel
                    )}
                  </TableCell> : null}
                  {isColumnVisible("plan") ? <TableCell className="align-top">
                    {planType ? (
                      <Badge
                        variant="outline"
                        className={PLAN_CLASS_MAP[planType] ?? PLAN_CLASS_MAP.free}
                      >
                        {planLabel}
                      </Badge>
                    ) : (
                      <span className="text-xs text-muted-foreground">--</span>
                    )}
                  </TableCell> : null}
                  {isColumnVisible("apiKey") ? <TableCell className="truncate align-top text-xs text-muted-foreground">
                    {request.apiKeyName || "--"}
                  </TableCell> : null}
                  {isColumnVisible("model") ? <TableCell className="truncate align-top">
                    <div className="leading-tight">
                      <span className="font-mono text-xs">
                        {formatModelLabel(request.model, request.reasoningEffort, visibleServiceTier)}
                      </span>
                      {request.requestKind === "warmup" || request.requestKind === "limit_warmup" ? (
                        <div className="mt-1 text-xs text-muted-foreground">
                          {REQUEST_KIND_LABELS.warmup}
                        </div>
                      ) : null}
                      {showRequestedTier ? (
                        <div className="text-[11px] text-muted-foreground">
                          {t("dashboard.requests.requestedTier", { tier: request.requestedServiceTier })}
                        </div>
                      ) : null}
                    </div>
                  </TableCell> : null}
                  {isColumnVisible("transport") ? <TableCell className="pr-3 align-top">
                    {request.transport ? (
                      <div className="space-y-1">
                        <Badge
                          variant="outline"
                          className={TRANSPORT_CLASS_MAP[request.transport] ?? TRANSPORT_CLASS_MAP.http}
                          title={t("dashboard.requests.downstreamTransport")}
                        >
                          {TRANSPORT_LABELS[request.transport] ?? request.transport}
                        </Badge>
                        {upstreamTransport ? (
                          <div className="text-[11px] text-muted-foreground">
                            {t("dashboard.requests.upstreamTransport", { transport: TRANSPORT_LABELS[upstreamTransport] ?? upstreamTransport })}
                          </div>
                        ) : null}
                      </div>
                    ) : (
                      <span className="text-xs text-muted-foreground">--</span>
                    )}
                  </TableCell> : null}
                  {isColumnVisible("status") ? <TableCell className="pl-3 align-top">
                    <Badge
                      variant="outline"
                      className={STATUS_CLASS_MAP[request.status] ?? STATUS_CLASS_MAP.error}
                    >
                      {t(`dashboard.requestStatus.${request.status}`, { defaultValue: REQUEST_STATUS_LABELS[request.status] ?? request.status })}
                    </Badge>
                  </TableCell> : null}
                  {isColumnVisible("ttft") ? <TableCell className="text-right align-top font-mono text-xs tabular-nums">
                    {formatCompactElapsed(request.latencyFirstTokenMs) ?? "--"}
                  </TableCell> : null}
                  {isColumnVisible("tps") ? <TableCell className="text-right align-top font-mono text-xs tabular-nums">
                    {generationSpeed ?? "--"}
                  </TableCell> : null}
                  {isColumnVisible("tokens") ? <TableCell className="text-right align-top font-mono text-xs tabular-nums">
                    <div className="leading-tight">
                      <div>{formatCompactNumber(request.tokens)}</div>
                      {request.cachedInputTokens != null && request.cachedInputTokens > 0 && (
                        <div className="text-[11px] text-muted-foreground">
                          {t("common.units.cachedShort", { count: formatCompactNumber(request.cachedInputTokens) })}
                        </div>
                      )}
                      {request.reasoningTokens != null ? (
                        <div className="text-[11px] text-muted-foreground">
                          {t("dashboard.requests.reasoningTokensShort", {
                            count: formatCompactNumber(request.reasoningTokens),
                          })}
                        </div>
                      ) : null}
                    </div>
                  </TableCell> : null}
                  {isColumnVisible("cost") ? <TableCell className="text-right align-top font-mono text-xs tabular-nums">
                    {formatCurrency(request.costUsd)}
                  </TableCell> : null}
                  {isColumnVisible("details") ? <TableCell className="pr-4 align-top whitespace-normal">
                    {hasError ? (
                      <div className="space-y-2">
                        {request.errorCode ? (
                          <div>
                            <Badge variant="outline" className="max-w-full font-mono text-[10px]">
                              <span className="truncate">{request.errorCode}</span>
                            </Badge>
                          </div>
                        ) : null}
                        <p className="line-clamp-2 break-words text-xs leading-relaxed text-muted-foreground">
                          {errorPreview}
                        </p>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-6 px-2 text-[11px]"
                          onClick={() => setSelectedRequest(request)}
                        >
                          {t("dashboard.requests.viewDetails")}
                        </Button>
                      </div>
                    ) : (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-6 px-2 text-[11px]"
                        onClick={() => setSelectedRequest(request)}
                      >
                        {t("dashboard.requests.viewDetails")}
                      </Button>
                    )}
                  </TableCell> : null}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </div>

      <div className="flex justify-end">
        <PaginationControls
          total={total}
          limit={limit}
          offset={offset}
          hasMore={hasMore}
          onLimitChange={onLimitChange}
          onOffsetChange={onOffsetChange}
        />
      </div>

      <Dialog open={selectedRequest !== null} onOpenChange={(open) => { if (!open) setSelectedRequest(null); }}>
        <DialogContent className="max-h-[85vh] grid-rows-[auto_minmax(0,1fr)] sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t("dashboard.requestDetails.title")}</DialogTitle>
            <DialogDescription>{t("dashboard.requestDetails.description")}</DialogDescription>
          </DialogHeader>
          <div className="grid min-h-0 gap-4 overflow-y-auto">
            <div className="space-y-3 rounded-md border bg-muted/30 p-4">
              <RequestDetailField
                label={t("dashboard.requestDetails.requestId")}
                value={selectedRequest?.requestId ?? "—"}
                mono
                copyValue={selectedRequest?.requestId ?? ""}
                copyLabel={t("dashboard.requestDetails.copyRequestId")}
                compactCopy
              />
              <div className="grid gap-3 sm:grid-cols-3">
                <RequestDetailField label={t("dashboard.requests.columns.status")} value={selectedRequest ? t(`dashboard.requestStatus.${selectedRequest.status}`, { defaultValue: REQUEST_STATUS_LABELS[selectedRequest.status] ?? selectedRequest.status }) : "—"} />
                <RequestDetailField label={t("dashboard.requests.columns.model")} value={selectedRequest ? formatModelLabel(selectedRequest.model, selectedRequest.reasoningEffort, selectedRequest.actualServiceTier ?? selectedRequest.serviceTier) : "—"} mono />
                <RequestDetailField label={t("dashboard.requestDetails.requestKind")} value={selectedRequest ? (REQUEST_KIND_LABELS[selectedRequest.requestKind] ?? selectedRequest.requestKind) : "—"} />
                <RequestDetailField label={t("dashboard.requests.columns.plan")} value={selectedRequest?.planType ? formatSlug(selectedRequest.planType) : "—"} />
                <RequestDetailField label={t("dashboard.requestDetails.elapsed")} value={formatElapsed(selectedRequest?.latencyMs ?? null)} />
                <RequestDetailField label="TTFT" value={formatElapsed(selectedRequest?.latencyFirstTokenMs ?? null)} />
                <RequestDetailField label={t("dashboard.requestDetails.queue")} value={formatElapsed(selectedRequest?.latencyQueueMs ?? null)} />
                <RequestDetailField label="TPS" value={selectedRequest ? (formatGenerationSpeed(selectedRequest) ?? "—") : "—"} />
                {selectedRequest?.reasoningTokens != null ? (
                  <RequestDetailField
                    label={t("dashboard.requestDetails.reasoningTokensIncluded")}
                    value={String(selectedRequest.reasoningTokens)}
                    mono
                  />
                ) : null}
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                <RequestDetailField label={t("dashboard.requests.columns.transport")} value={selectedRequest?.transport ? (TRANSPORT_LABELS[selectedRequest.transport] ?? selectedRequest.transport) : "—"} />
                <RequestDetailField label={t("dashboard.requests.columns.time")} value={selectedRequest ? formatDateTimeInline(selectedRequest.requestedAt, dateDisplayFormat) : "—"} />
                <RequestDetailField label={t("dashboard.requestDetails.errorCode")} value={selectedRequest?.errorCode ?? "—"} mono />
              </div>
              {selectedRequest?.upstreamProxyRouteMode ||
              selectedRequest?.upstreamProxyPoolId ||
              selectedRequest?.upstreamProxyEndpointId ||
              selectedRequest?.upstreamProxyFallbackUsed != null ||
              selectedRequest?.upstreamProxyFailClosedReason ? (
                <div className="grid gap-3 sm:grid-cols-3">
                  {selectedRequest.upstreamProxyRouteMode ? (
                    <RequestDetailField label={t("dashboard.requestDetails.routeMode")} value={selectedRequest.upstreamProxyRouteMode} mono />
                  ) : null}
                  {selectedRequest.upstreamProxyPoolId ? (
                    <RequestDetailField label={t("dashboard.requestDetails.routePool")} value={selectedRequest.upstreamProxyPoolId} mono />
                  ) : null}
                  {selectedRequest.upstreamProxyEndpointId ? (
                    <RequestDetailField label={t("dashboard.requestDetails.routeEndpoint")} value={selectedRequest.upstreamProxyEndpointId} mono />
                  ) : null}
                  {selectedRequest.upstreamProxyFallbackUsed != null ? (
                    <RequestDetailField
                      label={t("dashboard.requestDetails.routeFallback")}
                      value={t(
                        selectedRequest.upstreamProxyFallbackUsed
                          ? "dashboard.requestDetails.routeFallbackUsed"
                          : "dashboard.requestDetails.routeFallbackNotUsed",
                      )}
                    />
                  ) : null}
                  {selectedRequest.upstreamProxyFailClosedReason ? (
                    <RequestDetailField
                      label={t("dashboard.requestDetails.routeFailClosedReason")}
                      value={selectedRequest.upstreamProxyFailClosedReason}
                      mono
                    />
                  ) : null}
                </div>
              ) : null}
              {isAdmin ? (
                <RequestDetailField
                  label={t("dashboard.requestDetails.userAgent")}
                  value={selectedRequest?.useragent ?? "—"}
                  copyValue={selectedRequest?.useragent ?? undefined}
                  copyLabel={t("dashboard.requestDetails.copyUserAgent")}
                  compactCopy
                />
              ) : null}
              {isAdmin ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  <RequestDetailField
                    label={t("dashboard.requestDetails.clientIp")}
                    value={selectedRequest?.clientIp ?? "—"}
                    copyValue={selectedRequest?.clientIp ?? undefined}
                    copyLabel={t("dashboard.requestDetails.copyClientIp")}
                    compactCopy
                  />
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
                        {t("dashboard.requestDetails.conversationId")}
                      </div>
                      {selectedRequest?.conversationId ? (
                        <CopyButton value={selectedRequest.conversationId} label={t("dashboard.requestDetails.copyConversationId")} iconOnly />
                      ) : null}
                    </div>
                    <div className="flex flex-col items-start gap-2">
                      {selectedRequest?.conversationId ? (
                        onConversationClick ? (
                          <button
                            type="button"
                            className="max-w-[200px] truncate text-left text-sm leading-relaxed text-primary hover:text-primary/80 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring rounded-sm"
                            title={selectedRequest.conversationId}
                            onClick={() => {
                              setSelectedRequest(null);
                              onConversationClick(selectedRequest?.conversationId ?? "");
                            }}
                            aria-label={t("dashboard.filters.conversationFilterAria", { id: selectedRequest.conversationId })}
                          >
                            {selectedRequest.conversationId}
                          </button>
                        ) : (
                          <p className="max-w-[200px] truncate text-sm leading-relaxed" title={selectedRequest.conversationId ?? undefined}>
                            {selectedRequest.conversationId}
                          </p>
                        )
                      ) : (
                        <p className="min-w-0 flex-1 break-all text-sm leading-relaxed">—</p>
                      )}
                    </div>
                  </div>
                </div>
              ) : null}
            </div>

            {isAdmin ? (
              <RequestArchivePanel
                requestId={selectedRequest?.archiveRequestId ?? selectedRequest?.requestId}
                requestedAt={selectedRequest?.requestedAt}
              />
            ) : null}

            {selectedRequestCostSummary ? (
              <div className="space-y-2">
                <h3 className="text-sm font-medium">{t("dashboard.requests.columns.cost")}</h3>
                <div className="rounded-md bg-muted/50 p-3">
                  <p className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">
                    {selectedRequestCostSummary}
                  </p>
                </div>
              </div>
            ) : null}

            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-medium">{t("dashboard.requestDetails.fullError")}</h3>
                {selectedRequest?.errorMessage ? (
                  <CopyButton value={selectedRequest.errorMessage} label={t("dashboard.requestDetails.copyError")} iconOnly />
                ) : null}
              </div>
              <div className="max-h-[36vh] overflow-y-auto rounded-md bg-muted/50 p-3">
                <p className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">
                  {selectedRequest?.errorMessage ?? selectedRequest?.errorCode ?? t("dashboard.requestDetails.noErrorDetail")}
                </p>
              </div>
            </div>
          </div>
          <DialogFooter showCloseButton />
        </DialogContent>
      </Dialog>
    </div>
  );
}

type RequestDetailFieldProps = {
  label: string;
  value: string;
  mono?: boolean;
  copyValue?: string;
  copyLabel?: string;
  compactCopy?: boolean;
};

function RequestDetailField({
  label,
  value,
  mono = false,
  copyValue,
  copyLabel,
  compactCopy = false,
}: RequestDetailFieldProps) {
  const { t } = useTranslation();
  const copyLabelText = copyLabel ?? t("components.copyButton.copy");

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
          {label}
        </div>
        {copyValue ? (
          <CopyButton value={copyValue} label={copyLabelText} iconOnly={compactCopy} />
        ) : null}
      </div>
      <div className="flex flex-col items-start gap-2">
        <p className={`min-w-0 flex-1 break-all text-sm leading-relaxed ${mono ? "font-mono" : ""}`}>
          {value}
        </p>
      </div>
    </div>
  );
}
