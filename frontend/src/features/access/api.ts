import { z } from "zod";

import { del, get, patch, post } from "@/lib/api-client";

// Wire shapes of `app/modules/dashboard_users/schemas.py` and
// `app/modules/dashboard_roles/schemas.py`. Never carries a hash or a secret;
// the invite token appears exactly once, in `IssuedInviteSchema`.

const USERS_PATH = "/api/dashboard-users";
const ROLES_PATH = "/api/dashboard-roles";

export const RoleSummarySchema = z.object({
  id: z.string(),
  slug: z.string(),
  name: z.string(),
  kind: z.string(),
});

export const DashboardUserStatusSchema = z.enum(["active", "disabled", "invited"]);

export const DashboardUserSchema = z.object({
  id: z.string(),
  username: z.string(),
  displayName: z.string().nullable().default(null),
  email: z.string().nullable().default(null),
  role: RoleSummarySchema,
  roleSource: z.string(),
  status: DashboardUserStatusSchema,
  isBreakGlass: z.boolean(),
  totpConfigured: z.boolean(),
  hasPassword: z.boolean(),
  createdAt: z.string(),
  lastLoginAt: z.string().nullable().default(null),
  // `expiresAt` is null for an SSO-only account: it waits for its first provider sign-in.
  pendingInvite: z
    .object({ expiresAt: z.string().nullable().default(null), ssoOnly: z.boolean().default(false) })
    .nullable()
    .default(null),
});

export const IssuedInviteSchema = z.object({
  token: z.string(),
  expiresAt: z.string(),
});

export const DashboardUserCreateResponseSchema = z.object({
  user: DashboardUserSchema,
  // `null` for an SSO-only account: there is no link to hand over.
  invite: IssuedInviteSchema.nullable(),
});

export const PendingInviteSchema = z.object({
  userId: z.string(),
  username: z.string(),
  roleId: z.string(),
  expiresAt: z.string().nullable().default(null),
  createdByUserId: z.string(),
  ssoOnly: z.boolean().default(false),
});

export const DashboardRoleSchema = z.object({
  id: z.string(),
  slug: z.string(),
  name: z.string(),
  description: z.string().nullable().default(null),
  kind: z.string(),
  locked: z.boolean(),
  assignableToUsers: z.boolean(),
  grants: z.array(z.object({ permission: z.string(), scope: z.string() })),
  usersCount: z.number().int(),
});

export const PermissionDescriptorSchema = z.object({
  permission: z.string(),
  description: z.string(),
  implies: z.array(z.string()),
  ownSupported: z.boolean(),
  privileged: z.boolean(),
});

// Mirrors the backend username rule (`[a-z0-9._-]`, 1-64) so the dialog can
// refuse a bad name before the round-trip.
export const DashboardUserCreateRequestSchema = z.object({
  username: z
    .string()
    .trim()
    .min(1, { message: "access.invite.validation.usernameRequired" })
    .max(64, { message: "access.invite.validation.usernameMax" })
    .regex(/^[a-z0-9._-]+$/i, { message: "access.invite.validation.usernamePattern" })
    .transform((value) => value.toLowerCase()),
  displayName: z.string().trim().max(128, { message: "access.invite.validation.displayNameMax" }).optional(),
  // Empty until the roles list has loaded; the dialog fills in the preselected role.
  roleId: z.string(),
  // SSO-only: no link, the account is activated by its first provider sign-in
  // matching `expectedIdentity` exactly (needs an active non-password provider).
  ssoOnly: z.boolean().optional(),
  expectedIdentity: z.object({ provider: z.string(), providerKey: z.string(), subject: z.string() }).optional(),
});

export type DashboardUser = z.infer<typeof DashboardUserSchema>;
export type DashboardUserStatus = z.infer<typeof DashboardUserStatusSchema>;
export type IssuedInvite = z.infer<typeof IssuedInviteSchema>;
export type PendingInvite = z.infer<typeof PendingInviteSchema>;
export type DashboardRole = z.infer<typeof DashboardRoleSchema>;
export type PermissionDescriptor = z.infer<typeof PermissionDescriptorSchema>;
export type DashboardUserCreateRequest = z.infer<typeof DashboardUserCreateRequestSchema>;
export type DashboardUserUpdateRequest = {
  /** A new username. Validated like one chosen at creation; `409 username_taken` on collision. */
  username?: string;
  roleId?: string;
  status?: "active" | "disabled";
  /** Take a role the company login manages back under manual control (`409 role_managed_externally` otherwise). */
  force?: boolean;
};

const StatusSchema = z.object({ status: z.string() });

export function listDashboardUsers() {
  return get(USERS_PATH, z.array(DashboardUserSchema));
}

export function createDashboardUser(payload: DashboardUserCreateRequest) {
  return post(USERS_PATH, DashboardUserCreateResponseSchema, { body: payload });
}

export function updateDashboardUser(userId: string, payload: DashboardUserUpdateRequest) {
  return patch(`${USERS_PATH}/${encodeURIComponent(userId)}`, DashboardUserSchema, { body: payload });
}

export function deleteDashboardUser(userId: string) {
  return del(`${USERS_PATH}/${encodeURIComponent(userId)}`);
}

export function listPendingInvites() {
  return get(`${USERS_PATH}/invites`, z.array(PendingInviteSchema));
}

/** Rotates the invite token; the old link dies immediately. */
export function resendInvite(userId: string) {
  return post(`${USERS_PATH}/${encodeURIComponent(userId)}/invite`, IssuedInviteSchema);
}

/** Kills the link and removes the still-invited account row. */
export function revokeInvite(userId: string) {
  return del(`${USERS_PATH}/${encodeURIComponent(userId)}/invite`);
}

export function resetUserTotp(userId: string) {
  return post(`${USERS_PATH}/${encodeURIComponent(userId)}/reset-totp`, StatusSchema);
}

export function revokeUserSessions(userId: string) {
  return post(`${USERS_PATH}/${encodeURIComponent(userId)}/revoke-sessions`, StatusSchema);
}

export function listDashboardRoles() {
  return get(ROLES_PATH, z.array(DashboardRoleSchema));
}

export function listPermissionDescriptors() {
  return get(`${ROLES_PATH}/permissions`, z.array(PermissionDescriptorSchema));
}

/** The link an invited person opens; the token is the only secret in it. */
export function inviteLinkFor(token: string): string {
  return `${window.location.origin}/invite/${encodeURIComponent(token)}`;
}
