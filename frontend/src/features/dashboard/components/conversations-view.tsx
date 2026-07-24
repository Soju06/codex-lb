import { Search } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SpinnerBlock } from "@/components/ui/spinner";
import { ConversationDetailsDialog } from "@/features/dashboard/components/conversation-details-dialog";
import { ConversationTable } from "@/features/dashboard/components/conversation-table";
import { useConversations, type UseConversationsResult } from "@/features/dashboard/hooks/use-conversations";

type ConversationsViewProps = {
  state?: UseConversationsResult;
};

export function ConversationsView({ state }: ConversationsViewProps) {
  if (state) {
    return <ConversationsViewContent state={state} />;
  }
  return <StandaloneConversationsView />;
}

function StandaloneConversationsView() {
  const state = useConversations({ enabled: true });
  return <ConversationsViewContent state={state} />;
}

function ConversationsViewContent({ state }: { state: UseConversationsResult }) {
  const { t } = useTranslation();
  const { filters, conversationsQuery, updateFilters } = state;
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);

  const data = conversationsQuery.data;
  const errorMessage = conversationsQuery.error instanceof Error
    ? conversationsQuery.error.message
    : t("dashboard.conversations.errorDescription");

  return (
    <div className="space-y-3">
      <div className="rounded-xl border bg-card p-4 shadow-sm shadow-black/[0.02] dark:shadow-black/20">
        <div className="relative max-w-2xl">
          <Search className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground/60" aria-hidden="true" />
          <Input
            type="search"
            name="conversationSearch"
            autoComplete="off"
            value={filters.search}
            onChange={(event) => updateFilters({ search: event.target.value, offset: 0 })}
            className="h-9 pl-9"
            placeholder={t("dashboard.conversations.searchPlaceholder")}
            aria-label={t("dashboard.conversations.searchAria")}
          />
        </div>
      </div>

      {conversationsQuery.error ? (
        <div className="space-y-3 rounded-xl border bg-card p-4">
          <div role="alert">
            <AlertMessage variant="error">{errorMessage}</AlertMessage>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={() => void conversationsQuery.refetch()} disabled={conversationsQuery.isFetching}>
            {t("common.actions.retry")}
          </Button>
        </div>
      ) : null}

      {!conversationsQuery.error && conversationsQuery.isPending && !data ? (
        <div className="rounded-xl border bg-card py-8">
          <SpinnerBlock />
        </div>
      ) : null}

      {data ? (
        <ConversationTable
          conversations={data.conversations}
          total={data.total}
          limit={filters.limit}
          offset={filters.offset}
          hasMore={data.hasMore}
          onLimitChange={(limit) => updateFilters({ limit, offset: 0 })}
          onOffsetChange={(offset) => updateFilters({ offset })}
          onSelect={setSelectedConversationId}
        />
      ) : null}

      <ConversationDetailsDialog
        open={selectedConversationId !== null}
        conversationId={selectedConversationId}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedConversationId(null);
          }
        }}
      />
    </div>
  );
}
