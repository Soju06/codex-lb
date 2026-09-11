import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";

import {
  createRoleMapping,
  deleteRoleMapping,
  listAssignableRoles,
  listAuditEntries,
  listAuthProviders,
  listRoleMappings,
  reorderRoleMappings,
  updateAuthProvider,
  updateRoleMapping,
  type AuthProviderUpdateRequest,
  type RoleMappingCreateRequest,
  type RoleMappingUpdateRequest,
} from "@/features/organisation/api";
import { REFUSED_ACTION, REFUSED_REASON, refusedSince } from "@/features/organisation/rules";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { ApiError } from "@/lib/api-client";
import { getErrorMessage } from "@/utils/errors";

export const PROVIDERS_QUERY_KEY = ["auth-providers", "list"] as const;
export const MAPPINGS_QUERY_KEY = ["role-mappings", "list"] as const;
export const REFUSED_SIGN_INS_QUERY_KEY = ["audit-logs", "refused-sign-ins"] as const;
export const ASSIGNABLE_ROLES_QUERY_KEY = ["role-mappings", "assignable-roles"] as const;

// Backend refusals this group explains in its own words; anything else surfaces
// the server message unchanged.
const EXPLAINED_ERROR_CODES = new Set([
  "admin_account_required",
  "insufficient_delegation",
  "mapping_exists",
  "mapping_limit_reached",
  "order_stale",
  "provider_not_found",
  "mapping_not_found",
  "role_not_assignable",
  "unknown_claim",
]);

export function organisationErrorMessage(error: unknown, t: TFunction): string {
  if (error instanceof ApiError && EXPLAINED_ERROR_CODES.has(error.code)) {
    return t(`organisation.errors.${error.code}`);
  }
  return getErrorMessage(error);
}

export function useAuthProviders(enabled = true) {
  return useQuery({ queryKey: PROVIDERS_QUERY_KEY, queryFn: listAuthProviders, enabled });
}

export function useRoleMappings(enabled = true) {
  return useQuery({ queryKey: MAPPINGS_QUERY_KEY, queryFn: listRoleMappings, enabled });
}

/**
 * The roles this caller may hand out, from the rules API rather than the
 * `users:manage` roles list: a custom role holding only `security:write` owns
 * this group and must be able to name, and choose, what its rules give.
 */
export function useAssignableRoles(enabled = true) {
  return useQuery({ queryKey: ASSIGNABLE_ROLES_QUERY_KEY, queryFn: listAssignableRoles, enabled });
}

/**
 * The refused sign-ins of the last seven days. `audit:read` is a separate
 * permission from `security:write`, so a 403 here only costs the counter line;
 * the rules themselves stay editable.
 */
export function useRefusedSignIns(enabled = true) {
  return useQuery({
    queryKey: REFUSED_SIGN_INS_QUERY_KEY,
    queryFn: () =>
      listAuditEntries({ action: REFUSED_ACTION, reason: REFUSED_REASON, since: refusedSince(), limit: 50 }),
    enabled,
    retry: false,
  });
}

/**
 * Every write here changes how identities resolve, so it refreshes the session
 * too: `access_summary.role_mappings` and `providers_enabled` drive the group's
 * summary line and the disclosure tier.
 */
export function useOrganisationMutations() {
  const queryClient = useQueryClient();
  const settle = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: PROVIDERS_QUERY_KEY }),
      queryClient.invalidateQueries({ queryKey: MAPPINGS_QUERY_KEY }),
    ]);
    await useAuthStore.getState().refreshSession().catch(() => undefined);
  };

  const updateProvider = useMutation({
    mutationFn: ({ providerId, payload }: { providerId: string; payload: AuthProviderUpdateRequest }) =>
      updateAuthProvider(providerId, payload),
    onSuccess: settle,
  });
  const createMapping = useMutation({
    mutationFn: (payload: RoleMappingCreateRequest) => createRoleMapping(payload),
    onSuccess: settle,
  });
  const updateMapping = useMutation({
    mutationFn: ({ mappingId, payload }: { mappingId: string; payload: RoleMappingUpdateRequest }) =>
      updateRoleMapping(mappingId, payload),
    onSuccess: settle,
  });
  const removeMapping = useMutation({
    mutationFn: (mappingId: string) => deleteRoleMapping(mappingId),
    onSuccess: settle,
  });
  const reorderMappings = useMutation({
    mutationFn: (payload: { provider: string; providerKey: string; ids: string[] }) => reorderRoleMappings(payload),
    onSuccess: settle,
  });

  const busy = [updateProvider, createMapping, updateMapping, removeMapping, reorderMappings].some(
    (mutation) => mutation.isPending,
  );
  return { updateProvider, createMapping, updateMapping, removeMapping, reorderMappings, busy };
}
