import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  deleteStickySession,
  listStickySessions,
  purgeStickySessions,
} from "@/features/sticky-sessions/api";
import type { StickySessionIdentifier } from "@/features/sticky-sessions/schemas";

export function useStickySessions() {
  const queryClient = useQueryClient();

  const stickySessionsQuery = useQuery({
    queryKey: ["sticky-sessions", "list"],
    queryFn: listStickySessions,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["sticky-sessions", "list"] });
  };

  const deleteMutation = useMutation({
    mutationFn: (target: StickySessionIdentifier) => deleteStickySession(target),
    onSuccess: () => {
      toast.success("Sticky session removed");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to remove sticky session");
    },
  });

  const purgeMutation = useMutation({
    mutationFn: (staleOnly: boolean) => purgeStickySessions({ staleOnly }),
    onSuccess: (response) => {
      toast.success(`Purged ${response.deletedCount} sticky sessions`);
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to purge sticky sessions");
    },
  });

  return {
    stickySessionsQuery,
    deleteMutation,
    purgeMutation,
  };
}
