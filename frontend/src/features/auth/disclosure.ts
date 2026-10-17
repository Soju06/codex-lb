import type { AccessSummary, AuthSessionUser } from "@/features/auth/schemas";

/**
 * How much of the account machinery the dashboard shows. There is no stored
 * "mode": the tier is derived from what exists in the database (PLAN §4.11)
 * and every disclosure decision in the UI goes through this one function.
 *
 * - `individual`: one person. Nothing about accounts or roles is rendered.
 * - `team`: a second account, a pending invite, or a non-admin account exists.
 * - `enterprise`: an identity provider, custom role, SCIM token, audit sink,
 *   role mapping, or non-default local-login policy is configured.
 *
 * `accessSummary` is `null` for principals without `users:manage`. A signed-in
 * account (`user`) that does not manage users can only exist because someone
 * invited it, so that pair is the team rule evaluated from the one fact the
 * client may see; a null summary with no account (implicit admin, guest,
 * visitor) stays individual.
 */
export type DisclosureTier = "individual" | "team" | "enterprise";

export function resolveDisclosureTier(
  accessSummary: AccessSummary | null,
  user: AuthSessionUser | null,
): DisclosureTier {
  if (accessSummary === null) {
    return user === null ? "individual" : "team";
  }
  const enterprise =
    accessSummary.providersEnabled.some((kind) => kind !== "password") ||
    accessSummary.customRoles >= 1 ||
    accessSummary.scimTokens >= 1 ||
    accessSummary.auditSinks >= 1 ||
    accessSummary.localLoginPolicy !== "enabled" ||
    accessSummary.roleMappings >= 1;
  if (enterprise) {
    return "enterprise";
  }
  const team =
    accessSummary.usersTotal >= 2 || accessSummary.pendingInvites >= 1 || accessSummary.nonAdminUsers >= 1;
  return team ? "team" : "individual";
}
