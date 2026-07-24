import { Inbox } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { isEmailLabel } from "@/components/blur-email";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PaginationControls } from "@/features/dashboard/components/filters/pagination-controls";
import type { AccountSummary, ConversationEntry } from "@/features/dashboard/schemas";
import { usePrivacyStore } from "@/hooks/use-privacy";
import {
  formatCompactNumber,
  formatCurrency,
  formatTimeLong,
} from "@/utils/formatters";

export type ConversationTableProps = {
  conversations: ConversationEntry[];
  accounts: AccountSummary[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
  onLimitChange: (limit: number) => void;
  onOffsetChange: (offset: number) => void;
  onSelect: (conversationId: string) => void;
};

const headerClass = "text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80";

export function ConversationTable({
  conversations,
  accounts,
  total,
  limit,
  offset,
  hasMore,
  onLimitChange,
  onOffsetChange,
  onSelect,
}: ConversationTableProps) {
  const { t } = useTranslation();
  const blurred = usePrivacyStore((s) => s.blurred);
  const accountLabelMap = useMemo(() => {
    const labels = new Map<string, string>();
    for (const account of accounts) {
      labels.set(account.accountId, account.displayName || account.email || account.accountId);
    }
    return labels;
  }, [accounts]);

  /** Account IDs whose resolved label is an email. */
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

  if (conversations.length === 0) {
    return (
      <EmptyState
        icon={Inbox}
        title={t("dashboard.conversations.emptyTitle")}
        description={t("dashboard.conversations.emptyDescription")}
      />
    );
  }

  return (
    <div className="space-y-3">
      <div className="rounded-xl border bg-card shadow-sm shadow-black/[0.02] dark:shadow-black/20">
        <div className="relative overflow-x-auto">
          <Table className="min-w-[860px] table-fixed">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className={`w-[13%] pl-4 ${headerClass}`}>{t("dashboard.conversations.columns.lastRequest")}</TableHead>
                <TableHead className={`w-[19%] ${headerClass}`}>{t("dashboard.conversations.columns.conversation")}</TableHead>
                <TableHead className={`w-[14%] ${headerClass}`}>{t("dashboard.conversations.columns.accounts")}</TableHead>
                <TableHead className={`w-[13%] ${headerClass}`}>{t("dashboard.conversations.columns.apiKey")}</TableHead>
                <TableHead className={`w-[14%] ${headerClass}`}>{t("dashboard.conversations.columns.models")}</TableHead>
                <TableHead className={`w-[12%] text-right ${headerClass}`}>{t("dashboard.conversations.columns.tokens")}</TableHead>
                <TableHead className={`w-[8%] text-right ${headerClass}`}>{t("dashboard.conversations.columns.cost")}</TableHead>
                <TableHead className={`w-[7%] pr-4 ${headerClass}`}>{t("dashboard.conversations.columns.details")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {conversations.map((conversation) => {
                const time = formatTimeLong(conversation.lastRequest);
                const accountLabel = conversation.representativeAccount
                  ? (accountLabelMap.get(conversation.representativeAccount) ?? conversation.representativeAccount)
                  : null;
                const isEmailLabel = !!(
                  conversation.representativeAccount &&
                  emailLabelIds.has(conversation.representativeAccount)
                );

                return (
                  <TableRow key={conversation.conversationId} className="align-top">
                    <TableCell className="pl-4 align-top">
                      <div className="leading-tight">
                        <div className="text-sm font-medium">{time.time}</div>
                        <div className="text-xs text-muted-foreground">{time.date}</div>
                      </div>
                    </TableCell>
                    <TableCell className="max-w-0">
                      <span className="block truncate font-mono text-xs" title={conversation.conversationId}>
                        <span translate="no">{conversation.conversationId || "—"}</span>
                      </span>
                    </TableCell>
                    <TableCell className="align-top">
                      <AggregateValue
                        value={accountLabel}
                        remaining={conversation.remainingAccountCount}
                        blurred={blurred && isEmailLabel}
                      />
                    </TableCell>
                    <TableCell className="max-w-0 truncate align-top text-xs text-muted-foreground">
                      {conversation.apiKeyName || "—"}
                    </TableCell>
                    <TableCell className="align-top">
                      <AggregateValue
                        value={conversation.representativeModel}
                        remaining={conversation.remainingModelCount}
                        mono
                        translateNo
                      />
                    </TableCell>
                    <TableCell className="text-right align-top font-mono text-xs tabular-nums">
                      <div>{formatCompactNumber(conversation.totalTokens)}</div>
                      <div className="mt-1 text-[11px] font-sans text-muted-foreground">
                        {t("dashboard.conversations.cached", {
                          count: formatCachedTokenCount(conversation.cachedInputTokens),
                        })}
                      </div>
                    </TableCell>
                    <TableCell className="text-right align-top font-mono text-xs tabular-nums">
                      {formatCurrency(conversation.totalCostUsd)}
                    </TableCell>
                    <TableCell className="pr-4 align-top">
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-[11px]"
                        onClick={() => onSelect(conversation.conversationId)}
                        aria-label={t("dashboard.conversations.viewDetailsAria", {
                          id: conversation.conversationId,
                        })}
                      >
                        {t("dashboard.conversations.viewDetails")}
                      </Button>
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
    </div>
  );
}

function AggregateValue({
  value,
  remaining,
  mono = false,
  translateNo = false,
  blurred = false,
}: {
  value: string | null | undefined;
  remaining: number;
  mono?: boolean;
  translateNo?: boolean;
  blurred?: boolean;
}) {
  const { t } = useTranslation();

  return (
    <div className="min-w-0 leading-tight">
      <div className={`truncate text-sm ${mono ? "font-mono text-xs" : ""}`} title={blurred ? undefined : value ?? undefined}>
        {blurred ? (
          <span className="privacy-blur" translate={translateNo ? "no" : undefined}>{value || "—"}</span>
        ) : (
          <span translate={translateNo ? "no" : undefined}>{value || "—"}</span>
        )}
      </div>
      {remaining > 0 ? (
        <div className="mt-1 text-[11px] text-muted-foreground">
          {t("dashboard.conversations.more", { count: remaining })}
        </div>
      ) : null}
    </div>
  );
}

function formatCachedTokenCount(value: number | null | undefined): string {
  return value == null ? "—" : formatCompactNumber(value);
}
