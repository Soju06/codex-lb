import { Inbox } from "lucide-react";
import { useMemo, useState } from "react";
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
import type { AccountSummary, RequestLog } from "@/features/dashboard/schemas";
import { REQUEST_STATUS_LABELS } from "@/utils/constants";
import {
  formatDateTimeInline,
  formatCompactNumber,
  formatCurrency,
  formatModelLabel,
  formatElapsed,
  formatSlug,
  formatTimeLong,
} from "@/utils/formatters";

const STATUS_CLASS_MAP: Record<string, string> = {
  ok: "bg-emerald-500/15 text-emerald-700 border-emerald-500/20 hover:bg-emerald-500/20 dark:text-emerald-400",
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
  onLimitChange: (limit: number) => void;
  onOffsetChange: (offset: number) => void;
};

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
  onLimitChange,
  onOffsetChange,
}: RecentRequestsTableProps) {
  const { t } = useTranslation();
  const [selectedRequest, setSelectedRequest] = useState<RequestLog | null>(null);
  const blurred = usePrivacyStore((s) => s.blurred);
  const selectedRequestCostSummary = formatRequestCostSummary(selectedRequest, t);

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
    return (
      <EmptyState
        icon={Inbox}
        title={t("dashboard.requests.emptyTitle")}
        description={t("dashboard.requests.emptyDescription")}
      />
    );
  }

  return (
    <div className="space-y-3">
    <div className="rounded-xl border bg-card">
      <div className="relative overflow-x-auto">
        <Table className="w-full table-fixed">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="w-28 pl-4 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("dashboard.requests.columns.time")}</TableHead>
              <TableHead className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("dashboard.requests.columns.account")}</TableHead>
              <TableHead className="w-24 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("dashboard.requests.columns.plan")}</TableHead>
              <TableHead className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("dashboard.requests.columns.apiKey")}</TableHead>
              <TableHead className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("dashboard.requests.columns.model")}</TableHead>
              <TableHead className="w-32 pr-3 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("dashboard.requests.columns.transport")}</TableHead>
              <TableHead className="w-24 pl-3 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("dashboard.requests.columns.status")}</TableHead>
              <TableHead className="w-20 text-right text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">TTFT</TableHead>
              <TableHead className="w-20 text-right text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">TPS</TableHead>
              <TableHead className="w-24 text-right text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("dashboard.requests.columns.tokens")}</TableHead>
              <TableHead className="w-16 text-right text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("dashboard.requests.columns.cost")}</TableHead>
              <TableHead className="w-72 pr-4 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">{t("dashboard.requests.columns.details")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {requests.map((request) => {
              const time = formatTimeLong(request.requestedAt);
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
                  <TableCell className="pl-4 align-top">
                    <div className="leading-tight">
                      <div className="text-sm font-medium">{time.time}</div>
                      <div className="text-xs text-muted-foreground">{time.date}</div>
                    </div>
                  </TableCell>
                  <TableCell className="truncate align-top text-sm">
                    {isEmailLabel && blurred ? (
                      <span className="privacy-blur">{accountLabel}</span>
                    ) : (
                      accountLabel
                    )}
                  </TableCell>
                  <TableCell className="align-top">
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
                  </TableCell>
                  <TableCell className="truncate align-top text-xs text-muted-foreground">
                    {request.apiKeyName || "--"}
                  </TableCell>
                  <TableCell className="truncate align-top">
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
                  </TableCell>
                  <TableCell className="pr-3 align-top">
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
                  </TableCell>
                  <TableCell className="pl-3 align-top">
                    <Badge
                      variant="outline"
                      className={STATUS_CLASS_MAP[request.status] ?? STATUS_CLASS_MAP.error}
                    >
                      {t(`dashboard.requestStatus.${request.status}`, { defaultValue: REQUEST_STATUS_LABELS[request.status] ?? request.status })}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right align-top font-mono text-xs tabular-nums">
                    {formatCompactElapsed(request.latencyFirstTokenMs) ?? "--"}
                  </TableCell>
                  <TableCell className="text-right align-top font-mono text-xs tabular-nums">
                    {generationSpeed ?? "--"}
                  </TableCell>
                  <TableCell className="text-right align-top font-mono text-xs tabular-nums">
                    <div className="leading-tight">
                      <div>{formatCompactNumber(request.tokens)}</div>
                      {request.cachedInputTokens != null && request.cachedInputTokens > 0 && (
                        <div className="text-[11px] text-muted-foreground">
                          {t("common.units.cachedShort", { count: formatCompactNumber(request.cachedInputTokens) })}
                        </div>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="text-right align-top font-mono text-xs tabular-nums">
                    {formatCurrency(request.costUsd)}
                  </TableCell>
                  <TableCell className="pr-4 align-top whitespace-normal">
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
                  </TableCell>
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
        <DialogContent className="max-h-[85vh] sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t("dashboard.requestDetails.title")}</DialogTitle>
            <DialogDescription>{t("dashboard.requestDetails.description")}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 overflow-y-auto">
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
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                <RequestDetailField label={t("dashboard.requests.columns.transport")} value={selectedRequest?.transport ? (TRANSPORT_LABELS[selectedRequest.transport] ?? selectedRequest.transport) : "—"} />
                <RequestDetailField label={t("dashboard.requests.columns.time")} value={selectedRequest ? formatDateTimeInline(selectedRequest.requestedAt) : "—"} />
                <RequestDetailField label={t("dashboard.requestDetails.errorCode")} value={selectedRequest?.errorCode ?? "—"} mono />
              </div>
              <RequestDetailField
                label={t("dashboard.requestDetails.userAgent")}
                value={selectedRequest?.useragent ?? "—"}
                copyValue={selectedRequest?.useragent ?? undefined}
                copyLabel={t("dashboard.requestDetails.copyUserAgent")}
                compactCopy
              />
              <RequestDetailField
                label={t("dashboard.requestDetails.clientIp")}
                value={selectedRequest?.clientIp ?? "—"}
                copyValue={selectedRequest?.clientIp ?? undefined}
                copyLabel={t("dashboard.requestDetails.copyClientIp")}
                compactCopy
              />
            </div>

            <RequestArchivePanel
              requestId={selectedRequest?.archiveRequestId ?? selectedRequest?.requestId}
              requestedAt={selectedRequest?.requestedAt}
            />

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
