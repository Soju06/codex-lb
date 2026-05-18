import { useQuery } from "@tanstack/react-query";

import { getRequestLogVisibility } from "@/features/dashboard/api";

export function useRequestLogVisibility(logId: number | null, enabled = true) {
  return useQuery({
    queryKey: ["dashboard", "request-log-visibility", logId],
    queryFn: () => {
      if (logId === null) {
        throw new Error("logId is required");
      }
      return getRequestLogVisibility(logId);
    },
    enabled: enabled && logId !== null,
    staleTime: 30_000,
  });
}
