import { z } from "zod";

import { del, get, patch, post, put } from "@/lib/api-client";

// Wire shapes of `app/modules/auth_providers/schemas.py`,
// `app/modules/role_mappings/schemas.py` and the filtered read of
// `app/modules/audit/api.py`.
//
// Nothing the server SENDS here carries a secret: the trusted-header row's
// `config` holds header NAMES, the OIDC row's comes back with its client
// secret masked (`****last4`), and the audit rows are refusals. The request
// side is where a secret does travel: `OidcConfigRequest` carries the client
// secret in clear, because a connection document is written whole and the
// server will not inherit the old secret behind a repointed issuer. The mask
// is never sent back, and the secret is never stored anywhere but the form
// state of the dialog that collected it.

const PROVIDERS_PATH = "/api/auth-providers";
const MAPPINGS_PATH = "/api/role-mappings";
const AUDIT_PATH = "/api/audit-logs";
const OIDC_TEST_LOGIN_START_PATH = "/api/dashboard-auth/oidc/test-login/start";

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
  // When an admin last completed a test sign-in against the stored connection.
  // The API deliberately does not say *whose* proof it is, so this timestamp is
  // evidence that one exists, never that this browser may spend it.
  testLoginVerifiedAt: z.string().nullable().default(null),
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

/**
 * The OIDC connection document, as `OidcConfigRequest` wants it. It is written
 * whole every time — including `clientSecret`, which the server requires in
 * full rather than inheriting, so a repointed issuer cannot be handed the
 * credential its predecessor was given. Writing it clears any test-login proof.
 */
export type OidcConfigRequest = {
  issuer: string;
  discoveryUrl?: string | null;
  clientId: string;
  clientSecret: string;
  redirectUri: string;
  subjectClaim?: string | null;
  emailClaim?: string | null;
  nameClaim?: string | null;
  groupsClaim?: string | null;
};

export type AuthProviderUpdateRequest = {
  label?: string;
  enabled?: boolean;
  unknownIdentityRoleId?: string | null;
  noMatchRoleId?: string | null;
  linkByEmail?: boolean;
  skipRoleSync?: boolean;
  idpMfaEnforced?: boolean;
  config?: OidcConfigRequest;
};

/** Where the identity provider sends the browser back; the server validates the suffix. */
export const OIDC_CALLBACK_PATH = "/api/dashboard-auth/oidc/callback";

export const OidcStartSchema = z.object({ authorizationUrl: z.string() });

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

/**
 * Begin the pre-flight: the admin's own browser makes the round trip against
 * the connection as stored. The answer is only where to send the window; the
 * verdict arrives as a stamp on the provider row, never in this response.
 */
export function startOidcTestLogin() {
  return post(OIDC_TEST_LOGIN_START_PATH, OidcStartSchema);
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
