import { useQuery } from "@tanstack/react-query";

import { getRequestLogVisibility } from "@/features/dashboard/api";

export function useRequestLogVisibility(requestId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["dashboard", "request-log-visibility", requestId],
    queryFn: () => {
      if (!requestId) {
        throw new Error("requestId is required");
      }
      return getRequestLogVisibility(requestId);
    },
    enabled: enabled && !!requestId,
    staleTime: 30_000,
  });
}
