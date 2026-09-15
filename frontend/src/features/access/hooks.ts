import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";

import {
  createDashboardUser,
  deleteDashboardUser,
  listDashboardRoles,
  listDashboardUsers,
  listPendingInvites,
  listPermissionDescriptors,
  resendInvite,
  resetUserTotp,
  revokeInvite,
  revokeUserSessions,
  updateDashboardUser,
  type DashboardUserCreateRequest,
  type DashboardUserUpdateRequest,
} from "@/features/access/api";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { ApiError } from "@/lib/api-client";
import { getErrorMessage } from "@/utils/errors";

export const USERS_QUERY_KEY = ["dashboard-users", "list"] as const;
export const INVITES_QUERY_KEY = ["dashboard-users", "invites"] as const;
export const ROLES_QUERY_KEY = ["dashboard-roles", "list"] as const;
export const PERMISSIONS_QUERY_KEY = ["dashboard-roles", "permissions"] as const;

// Backend refusals the People tab explains in its own words; anything else
// surfaces the server message unchanged.
const EXPLAINED_ERROR_CODES = new Set([
  "admin_account_required",
  "credential_required",
  "email_taken",
  "insufficient_delegation",
  "invite_not_pending",
  "invite_pending",
  "last_admin_protected",
  // The break-glass guard refuses here too: role change, disable, delete,
  // clearing the designation, admin reset-totp (PLAN §4.2).
  "last_break_glass_protected",
  "break_glass_requires_totp",
  "role_managed_externally",
  "role_not_assignable",
  "self_modification_forbidden",
  "user_not_active",
  "user_not_found",
  "username_taken",
]);

export function accessErrorMessage(error: unknown, t: TFunction): string {
  if (error instanceof ApiError && EXPLAINED_ERROR_CODES.has(error.code)) {
    return t(`access.errors.${error.code}`);
  }
  return getErrorMessage(error);
}

export function useDashboardUsers(enabled = true) {
  return useQuery({ queryKey: USERS_QUERY_KEY, queryFn: listDashboardUsers, enabled });
}

export function usePendingInvites(enabled = true) {
  return useQuery({ queryKey: INVITES_QUERY_KEY, queryFn: listPendingInvites, enabled });
}

export function useDashboardRoles(enabled = true) {
  return useQuery({ queryKey: ROLES_QUERY_KEY, queryFn: listDashboardRoles, enabled });
}

export function usePermissionDescriptors(enabled = true) {
  return useQuery({ queryKey: PERMISSIONS_QUERY_KEY, queryFn: listPermissionDescriptors, enabled });
}

export type AccessMutationOptions = {
  onMutate?: () => void;
  onError?: (error: unknown) => void;
};

/**
 * Every people mutation invalidates the users, invites and roles lists.
 * Mutations that may change the team-size facts the Access card's tier is
 * derived from also refresh the session — except `createUser`: the caller
 * refreshes once the one-time invite link has been acknowledged, otherwise the
 * tier flip would unmount the dialog showing it.
 */
export function useAccessMutations({ onMutate, onError }: AccessMutationOptions = {}) {
  const queryClient = useQueryClient();
  const invalidate = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: USERS_QUERY_KEY }),
      queryClient.invalidateQueries({ queryKey: INVITES_QUERY_KEY }),
      queryClient.invalidateQueries({ queryKey: ROLES_QUERY_KEY }),
    ]);
  const settle = async () => {
    await invalidate();
    await useAuthStore.getState().refreshSession().catch(() => undefined);
  };
  const handleError = (error: unknown) => {
    // The invite this row referred to is gone: reload the lists so the row goes too.
    if (error instanceof ApiError && error.code === "invite_not_pending") {
      void invalidate();
    }
    onError?.(error);
  };
  const shared = { onMutate, onSuccess: settle, onError: handleError };

  const createUser = useMutation({
    mutationFn: (payload: DashboardUserCreateRequest) => createDashboardUser(payload),
    onMutate,
    onSuccess: invalidate,
    onError: handleError,
  });
  const updateUser = useMutation({
    mutationFn: ({ userId, payload }: { userId: string; payload: DashboardUserUpdateRequest }) =>
      updateDashboardUser(userId, payload),
    ...shared,
  });
  const deleteUser = useMutation({ mutationFn: (userId: string) => deleteDashboardUser(userId), ...shared });
  const resend = useMutation({ mutationFn: (userId: string) => resendInvite(userId), ...shared });
  const revoke = useMutation({ mutationFn: (userId: string) => revokeInvite(userId), ...shared });
  const resetTotp = useMutation({ mutationFn: (userId: string) => resetUserTotp(userId), ...shared });
  const revokeSessions = useMutation({ mutationFn: (userId: string) => revokeUserSessions(userId), ...shared });

  const busy = [createUser, updateUser, deleteUser, resend, revoke, resetTotp, revokeSessions].some(
    (mutation) => mutation.isPending,
  );
  return { createUser, updateUser, deleteUser, resend, revoke, resetTotp, revokeSessions, busy };
}
