import { get } from "@/lib/api-client";

import {
  ConversationArchiveFileSchema,
  ConversationArchiveRecordsResponseSchema,
} from "@/features/conversation-archive/schemas";

const CONVERSATION_ARCHIVE_PATH = "/api/conversation-archive";

export type ConversationArchiveRecordParams = {
  file?: string;
  limit?: number;
  offset?: number;
  direction?: string;
  kind?: string;
  transport?: string;
  requestId?: string;
  requestedAt?: string;
};

export function listConversationArchiveFiles() {
  return get(`${CONVERSATION_ARCHIVE_PATH}/files`, ConversationArchiveFileSchema.array());
}

export function listConversationArchiveRecords(params: ConversationArchiveRecordParams) {
  const query = new URLSearchParams();
  if (params.file) {
    query.set("file", params.file);
  }
  if (typeof params.limit === "number") {
    query.set("limit", String(params.limit));
  }
  if (typeof params.offset === "number") {
    query.set("offset", String(params.offset));
  }
  if (params.direction) {
    query.set("direction", params.direction);
  }
  if (params.kind) {
    query.set("kind", params.kind);
  }
  if (params.transport) {
    query.set("transport", params.transport);
  }
  if (params.requestId) {
    query.set("requestId", params.requestId);
  }
  if (params.requestedAt) {
    query.set("requestedAt", params.requestedAt);
  }
  return get(`${CONVERSATION_ARCHIVE_PATH}/records?${query.toString()}`, ConversationArchiveRecordsResponseSchema);
}
