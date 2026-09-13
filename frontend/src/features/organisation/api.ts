import { z } from "zod";

import { del, get, patch, post, put } from "@/lib/api-client";

// Wire shapes of `app/modules/auth_providers/schemas.py`,
// `app/modules/role_mappings/schemas.py` and the filtered read of
// `app/modules/audit/api.py`. Nothing here carries a secret: the provider
// `config` map holds the reverse-proxy header NAMES, which are deployment
// topology, and the audit rows are refusals, not credentials.

const PROVIDERS_PATH = "/api/auth-providers";
const MAPPINGS_PATH = "/api/role-mappings";
const AUDIT_PATH = "/api/audit-logs";

export const AuthProviderSchema = z.object({
  id: z.string(),
  kind: z.string(),
  providerKey: z.string(),
  label: z.string(),
  enabled: z.boolean(),
  // `enabled` and admitted by the running auth mode. A reverse-proxy row on a
  // password install is inactive, which is a topology fact, not a fault.
  active: z.boolean(),
  unknownIdentityRoleId: z.string().nullable().default(null),
  noMatchRoleId: z.string().nullable().default(null),
  linkByEmail: z.boolean(),
  skipRoleSync: z.boolean(),
  idpMfaEnforced: z.boolean(),
  config: z.record(z.string(), z.string()).default({}),
  createdAt: z.string(),
  updatedAt: z.string(),
});

export const RoleMappingSchema = z.object({
  id: z.string(),
  provider: z.string(),
  providerKey: z.string(),
  claimName: z.string(),
  claimValue: z.string(),
  roleId: z.string(),
  // Server-owned and dense; the list arrives winner-first.
  priority: z.number().int(),
  createdAt: z.string(),
  updatedAt: z.string(),
});

/**
 * A role this caller may hand out here. Served by the rules API itself
 * because the full roles list is `users:manage`, a different permission from
 * the one that gates this group; the server has already applied the same
 * delegation rule its writes apply, so anything listed is safe to offer.
 */
export const AssignableRoleSchema = z.object({
  id: z.string(),
  slug: z.string(),
  name: z.string(),
  description: z.string().nullable().default(null),
  kind: z.string(),
  locked: z.boolean(),
});

export const AuditEntrySchema = z.object({
  id: z.number().int(),
  timestamp: z.string(),
  action: z.string(),
  actorIp: z.string().nullable().default(null),
  details: z.record(z.string(), z.unknown()).nullable().default(null),
  severity: z.string().default("info"),
});

export type AuthProvider = z.infer<typeof AuthProviderSchema>;
export type RoleMapping = z.infer<typeof RoleMappingSchema>;
export type AssignableRole = z.infer<typeof AssignableRoleSchema>;
export type AuditEntry = z.infer<typeof AuditEntrySchema>;

export type AuthProviderUpdateRequest = {
  unknownIdentityRoleId?: string | null;
  noMatchRoleId?: string | null;
  linkByEmail?: boolean;
  skipRoleSync?: boolean;
};

export type RoleMappingCreateRequest = {
  provider: string;
  providerKey: string;
  claimName: string;
  claimValue: string;
  roleId: string;
};

export type RoleMappingUpdateRequest = { claimValue?: string; roleId?: string };

export function listAuthProviders() {
  return get(PROVIDERS_PATH, z.array(AuthProviderSchema));
}

export function updateAuthProvider(providerId: string, payload: AuthProviderUpdateRequest) {
  return patch(`${PROVIDERS_PATH}/${encodeURIComponent(providerId)}`, AuthProviderSchema, { body: payload });
}

export function listRoleMappings() {
  return get(MAPPINGS_PATH, z.array(RoleMappingSchema));
}

export function listAssignableRoles() {
  return get(`${MAPPINGS_PATH}/assignable-roles`, z.array(AssignableRoleSchema));
}

export function createRoleMapping(payload: RoleMappingCreateRequest) {
  return post(MAPPINGS_PATH, RoleMappingSchema, { body: payload });
}

export function updateRoleMapping(mappingId: string, payload: RoleMappingUpdateRequest) {
  return patch(`${MAPPINGS_PATH}/${encodeURIComponent(mappingId)}`, RoleMappingSchema, { body: payload });
}

export function deleteRoleMapping(mappingId: string) {
  return del(`${MAPPINGS_PATH}/${encodeURIComponent(mappingId)}`);
}

/** The full order of one provider's rules, winner first. */
export function reorderRoleMappings(payload: { provider: string; providerKey: string; ids: string[] }) {
  return put(`${MAPPINGS_PATH}/order`, z.array(RoleMappingSchema), { body: payload });
}

export type AuditQuery = { action: string; reason: string; since: string; limit?: number };

/** The audit log, filtered. The rules card only ever asks for refused sign-ins. */
export function listAuditEntries({ action, reason, since, limit = 50 }: AuditQuery) {
  const query = new URLSearchParams({ action, reason, since, limit: String(limit) });
  return get(`${AUDIT_PATH}?${query.toString()}`, z.array(AuditEntrySchema));
}
