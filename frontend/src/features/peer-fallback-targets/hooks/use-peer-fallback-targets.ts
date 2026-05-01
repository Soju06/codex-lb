import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  createPeerFallbackTarget,
  deletePeerFallbackTarget,
  listPeerFallbackTargets,
  updatePeerFallbackTarget,
} from "@/features/peer-fallback-targets/api";
import type { PeerFallbackTargetUpdateRequest } from "@/features/peer-fallback-targets/schemas";

const QUERY_KEY = ["peer-fallback-targets", "list"] as const;

export function usePeerFallbackTargets() {
  const queryClient = useQueryClient();

  const targetsQuery = useQuery({
    queryKey: QUERY_KEY,
    queryFn: listPeerFallbackTargets,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: QUERY_KEY });
  };

  const createMutation = useMutation({
    mutationFn: (baseUrl: string) => createPeerFallbackTarget({ baseUrl, enabled: true }),
    onSuccess: () => {
      toast.success("Peer fallback target added");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to add peer fallback target");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ targetId, payload }: { targetId: string; payload: PeerFallbackTargetUpdateRequest }) =>
      updatePeerFallbackTarget(targetId, payload),
    onSuccess: () => {
      toast.success("Peer fallback target updated");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to update peer fallback target");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (targetId: string) => deletePeerFallbackTarget(targetId),
    onSuccess: () => {
      toast.success("Peer fallback target removed");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to remove peer fallback target");
    },
  });

  return {
    targetsQuery,
    createMutation,
    updateMutation,
    deleteMutation,
  };
}
