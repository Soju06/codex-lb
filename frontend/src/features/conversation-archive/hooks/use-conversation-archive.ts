import { useQuery } from "@tanstack/react-query";

import {
  listConversationArchiveRecords,
  type ConversationArchiveRecordParams,
} from "@/features/conversation-archive/api";

export function useConversationArchiveRecords(params: ConversationArchiveRecordParams | null) {
  return useQuery({
    queryKey: ["conversation-archive", "records", params],
    queryFn: () => listConversationArchiveRecords(params!),
    enabled: params !== null,
  });
}
