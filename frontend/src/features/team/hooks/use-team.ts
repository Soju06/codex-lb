import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  createTeamMember,
  deleteTeamMember,
  getTeamMemberOnboarding,
  issueTeamMemberKey,
  listTeamMembers,
  updateTeamMember,
} from "@/features/team/api";
import type {
  TeamMemberCreateRequest,
  TeamMemberKeyCreateRequest,
  TeamMemberUpdateRequest,
} from "@/features/team/schemas";

const TEAM_LIST_KEY = ["team", "members"] as const;

export function useTeamMembers() {
  const queryClient = useQueryClient();

  const membersQuery = useQuery({
    queryKey: TEAM_LIST_KEY,
    queryFn: listTeamMembers,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: TEAM_LIST_KEY });
  };

  const createMutation = useMutation({
    mutationFn: (payload: TeamMemberCreateRequest) => createTeamMember(payload),
    onSuccess: () => {
      toast.success("Team member added");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to add team member");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ memberId, payload }: { memberId: string; payload: TeamMemberUpdateRequest }) =>
      updateTeamMember(memberId, payload),
    onSuccess: () => {
      toast.success("Team member updated");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to update team member");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (memberId: string) => deleteTeamMember(memberId),
    onSuccess: () => {
      toast.success("Team member removed");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to remove team member");
    },
  });

  const issueKeyMutation = useMutation({
    mutationFn: ({ memberId, payload }: { memberId: string; payload?: TeamMemberKeyCreateRequest }) =>
      issueTeamMemberKey(memberId, payload ?? {}),
    onSuccess: () => {
      toast.success("API key issued");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to issue API key");
    },
  });

  return {
    membersQuery,
    createMutation,
    updateMutation,
    deleteMutation,
    issueKeyMutation,
  };
}

export function useTeamOnboarding(memberId: string | null) {
  return useQuery({
    queryKey: ["team", "onboarding", memberId],
    queryFn: () => getTeamMemberOnboarding(memberId as string),
    enabled: memberId !== null,
  });
}
