import { useQuery } from "@tanstack/react-query";

import {
  listConversationArchiveFiles,
  listConversationArchiveRecords,
  type ConversationArchiveRecordParams,
} from "@/features/conversation-archive/api";

export function useConversationArchiveFiles() {
  return useQuery({
    queryKey: ["conversation-archive", "files"],
    queryFn: listConversationArchiveFiles,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });
}

export function useConversationArchiveRecords(params: ConversationArchiveRecordParams | null) {
  return useQuery({
    queryKey: ["conversation-archive", "records", params],
    queryFn: () => listConversationArchiveRecords(params!),
    enabled: params !== null,
  });
}
