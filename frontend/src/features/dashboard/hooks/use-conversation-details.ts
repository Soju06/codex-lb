import { useQuery } from "@tanstack/react-query";

import { getConversationDetails } from "@/features/dashboard/api";

export function useConversationDetails(conversationId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["dashboard", "conversation-details", conversationId],
    queryFn: () => getConversationDetails(conversationId ?? ""),
    enabled: enabled && Boolean(conversationId),
    retry: false,
  });
}
