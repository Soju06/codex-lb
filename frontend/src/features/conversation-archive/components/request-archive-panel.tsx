import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useConversationArchiveRecords } from "@/features/conversation-archive/hooks/use-conversation-archive";
import type { ConversationArchiveRecord } from "@/features/conversation-archive/schemas";

const REQUEST_ARCHIVE_LIMIT = 200;

export function RequestArchivePanel({
  requestId,
  requestedAt,
}: {
  requestId: string | null | undefined;
  requestedAt?: string | null | undefined;
}) {
  const [expandedIndex, setExpandedIndex] = useState<number | null>(0);
  const recordsQuery = useConversationArchiveRecords(
    requestId
      ? {
          requestId,
          requestedAt: requestedAt ?? undefined,
          limit: REQUEST_ARCHIVE_LIMIT,
          offset: 0,
        }
      : null,
  );

  if (!requestId) {
    return null;
  }

  if (recordsQuery.isPending) {
    return (
      <section className="space-y-2">
        <h3 className="text-sm font-medium">Archive</h3>
        <Skeleton className="h-20 w-full" />
      </section>
    );
  }

  if (recordsQuery.isError) {
    return (
      <section className="space-y-2">
        <h3 className="text-sm font-medium">Archive</h3>
        <div className="rounded-md border border-destructive/25 bg-destructive/10 p-3 text-xs text-destructive">
          {recordsQuery.error instanceof Error ? recordsQuery.error.message : "Failed to load archive records"}
        </div>
      </section>
    );
  }

  const records = recordsQuery.data?.records ?? [];

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium">Archive</h3>
        <span className="text-xs text-muted-foreground">{recordsQuery.data?.total ?? 0} records</span>
      </div>
      {records.length === 0 ? (
        <div className="rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground">
          No archived payloads for this request.
        </div>
      ) : (
        <div className="max-h-[42vh] overflow-y-auto rounded-md border">
          {records.map((record, index) => {
            const expanded = expandedIndex === index;
            return (
              <div key={`${record.fileName ?? "archive"}-${record.timestamp ?? index}-${index}`} className="border-b last:border-b-0">
                <button
                  type="button"
                  className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-muted/60"
                  onClick={() => setExpandedIndex(expanded ? null : index)}
                >
                  {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                  <ArchiveRecordSummary record={record} />
                </button>
                {expanded ? (
                  <div className="grid gap-2 border-t bg-muted/20 p-3 md:grid-cols-2">
                    <JsonBlock title="Payload" value={record.payload} />
                    <JsonBlock title="Metadata" value={metadataForRecord(record)} />
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
      {recordsQuery.data?.hasMore ? (
        <div className="text-xs text-muted-foreground">Showing first {REQUEST_ARCHIVE_LIMIT} records.</div>
      ) : null}
    </section>
  );
}

function ArchiveRecordSummary({ record }: { record: ConversationArchiveRecord }) {
  return (
    <span className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
      <Badge variant="secondary">{record.direction ?? "-"}</Badge>
      <span className="font-mono text-xs">{record.kind ?? "-"}</span>
      <span className="text-xs text-muted-foreground">{record.transport ?? "-"}</span>
      <span className="truncate text-xs text-muted-foreground">{record.fileName ?? formatDateTime(record.timestamp)}</span>
    </span>
  );
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <div className="min-w-0 rounded-md border bg-background">
      <div className="border-b px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {title}
      </div>
      <pre className="max-h-72 overflow-auto p-3 text-xs leading-relaxed whitespace-pre-wrap">
        {stringifyJson(value)}
      </pre>
    </div>
  );
}

function metadataForRecord(record: ConversationArchiveRecord) {
  return {
    fileName: record.fileName,
    timestamp: record.timestamp,
    requestId: record.requestId,
    direction: record.direction,
    kind: record.kind,
    transport: record.transport,
    accountId: record.accountId,
    method: record.method,
    url: record.url,
    statusCode: record.statusCode,
    headers: record.headers,
    extra: record.extra,
  };
}

function stringifyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function formatDateTime(value: string | null): string {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}
