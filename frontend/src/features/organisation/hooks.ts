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
import { getSettings, updateSettings } from "@/features/settings/api";
import type { SettingsUpdateRequest } from "@/features/settings/schemas";
import { ApiError } from "@/lib/api-client";
import { getErrorMessage } from "@/utils/errors";

export const PROVIDERS_QUERY_KEY = ["auth-providers", "list"] as const;
export const MAPPINGS_QUERY_KEY = ["role-mappings", "list"] as const;
export const REFUSED_SIGN_INS_QUERY_KEY = ["audit-logs", "refused-sign-ins"] as const;
export const ASSIGNABLE_ROLES_QUERY_KEY = ["role-mappings", "assignable-roles"] as const;
/** The shared settings query key; the login-policy card reads and writes the same row. */
export const SETTINGS_QUERY_KEY = ["settings", "detail"] as const;

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
  // The break-glass guard, in both directions (PLAN §4.2/§4.6).
  "break_glass_requires_totp",
  "last_break_glass_protected",
]);

/**
 * The account a `break_glass_requires_totp` refusal names. The server sends it
 * so the refusal doubles as the instruction; nothing else in the envelope is
 * shown to the person.
 */
export function breakGlassAccountFromError(error: unknown): string | null {
  if (!(error instanceof ApiError)) {
    return null;
  }
  const envelope = error.details;
  if (typeof envelope !== "object" || envelope === null || !("details" in envelope)) {
    return null;
  }
  const details = (envelope as { details?: unknown }).details;
  if (typeof details !== "object" || details === null || !("username" in details)) {
    return null;
  }
  const username = (details as { username?: unknown }).username;
  return typeof username === "string" && username.length > 0 ? username : null;
}

export function organisationErrorMessage(error: unknown, t: TFunction): string {
  if (error instanceof ApiError && EXPLAINED_ERROR_CODES.has(error.code)) {
    return t(`organisation.errors.${error.code}`);
  }
  return getErrorMessage(error);
}

/** The settings row, for the one field this group owns (`local_login_policy`). */
export function useOrganisationSettings(enabled = true) {
  return useQuery({ queryKey: SETTINGS_QUERY_KEY, queryFn: getSettings, enabled });
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
  // Changing the login policy changes `access_summary.local_login_policy`,
  // which is what the disclosure tier and the collapsed summary line read.
  const updateLoginPolicy = useMutation({
    mutationFn: (payload: SettingsUpdateRequest) => updateSettings(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY });
      await settle();
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "settings_conflict") {
        // Another writer committed since the form loaded; refetch so a retry carries the fresh version.
        void queryClient.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY });
      }
    },
  });
  const reorderMappings = useMutation({
    mutationFn: (payload: { provider: string; providerKey: string; ids: string[] }) => reorderRoleMappings(payload),
    onSuccess: settle,
  });

  const busy = [updateProvider, createMapping, updateMapping, removeMapping, reorderMappings, updateLoginPolicy].some(
    (mutation) => mutation.isPending,
  );
  return { updateProvider, createMapping, updateMapping, removeMapping, reorderMappings, updateLoginPolicy, busy };
}
