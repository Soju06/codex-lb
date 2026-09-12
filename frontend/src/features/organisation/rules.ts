import type { AuditEntry, AuthProvider, RoleMapping } from "@/features/organisation/api";
import type { DashboardUser } from "@/features/access/api";
import type { AccessSummary } from "@/features/auth/schemas";

// Pure helpers behind the Organisation group. They live outside the components
// so the collapse contract, the ordering rules and the refused-sign-in window
// can be unit-tested without rendering anything.

/** The one provider kind this release configures; OIDC arrives in a later phase. */
export const TRUSTED_HEADER_KIND = "trusted_header";

/** Claim names `app/modules/role_mappings/matching.py` accepts today. */
export const CLAIM_GROUPS = "groups";
export const CLAIM_EMAIL_DOMAIN = "email_domain";

/** The audit window the rules card reports on. */
export const REFUSED_WINDOW_DAYS = 7;
export const REFUSED_ACTION = "login_failed";
export const REFUSED_REASON = "unknown_identity";

/** Inclusive lower bound of the refused-sign-in window, as the audit API wants it. */
export function refusedSince(now: Date = new Date()): string {
  return new Date(now.getTime() - REFUSED_WINDOW_DAYS * 24 * 3600_000).toISOString();
}

/**
 * Whether anything in this group has been set up yet. Derived from the session
 * facts the store already holds, so the collapsed group costs no request.
 * A summary the caller may not see (`null`, no `users:manage`) fails closed to
 * "nothing configured": the one-line label says nothing it should not.
 */
export function isOrganisationConfigured(summary: AccessSummary | null): boolean {
  return hasCompanyLogin(summary) || isLocalLoginRestricted(summary);
}

/** A sign-in method other than the local password, or a rule that routes one. */
export function hasCompanyLogin(summary: AccessSummary | null): boolean {
  if (summary === null) {
    return false;
  }
  return summary.providersEnabled.some((kind) => kind !== "password") || summary.roleMappings >= 1;
}

/**
 * The local password form is closed to somebody. It is a configured fact in
 * its own right — an install can restrict it with no company login at all
 * (the host CLI can set it) — so the collapsed line has to say so.
 */
export function isLocalLoginRestricted(summary: AccessSummary | null): boolean {
  return summary !== null && summary.localLoginPolicy !== "enabled";
}

/**
 * The five facts that make a break-glass *designation* a *qualifying* account
 * (`app/modules/dashboard_users/break_glass.py`): designated, active, on the
 * admin preset, holding a second factor, and holding a local password — an
 * account the proxy provisioned cannot use the local form the designation is
 * about. Computed here the same way the server computes it, so the card can
 * explain a refusal before it happens.
 */
export function isQualifyingBreakGlass(user: DashboardUser): boolean {
  return (
    user.isBreakGlass &&
    user.status === "active" &&
    user.role.slug === "admin" &&
    user.totpConfigured &&
    user.hasPassword
  );
}

/** Every account carrying the designation, qualifying or not; the card names these. */
export function breakGlassDesignations(users: readonly DashboardUser[] | undefined): DashboardUser[] {
  return (users ?? []).filter((user) => user.isBreakGlass);
}

/** Presets in the order people meet them; Guest last because it is not an account. */
const PRESET_ORDER = ["admin", "operator", "member", "viewer", "guest"];

/**
 * What a picker needs of a role. Both role reads satisfy it — the
 * `users:manage` list and the smaller `security:write` one — so the picker
 * does not care which permission the caller holds.
 */
export type PickerRole = { id: string; slug: string; name: string; kind: string };

export function isPresetRole(role: PickerRole): boolean {
  return role.kind === "preset";
}

/**
 * Presets first, in their canonical order, then custom roles alphabetically.
 * Custom roles are only offered once at least one exists, so an install that
 * never made one is not told a concept it does not have.
 */
export function orderRolesForPicker<T extends PickerRole>(
  roles: readonly T[],
  { customRoles }: { customRoles: number },
): T[] {
  const presets = roles
    .filter(isPresetRole)
    .sort((a, b) => PRESET_ORDER.indexOf(a.slug) - PRESET_ORDER.indexOf(b.slug));
  if (customRoles < 1) {
    return presets;
  }
  const custom = roles.filter((role) => !isPresetRole(role)).sort((a, b) => a.name.localeCompare(b.name));
  return [...presets, ...custom];
}

/** Moves one entry of a winner-first order; out-of-range moves are no-ops. */
export function moveInOrder<T>(items: readonly T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= items.length || to >= items.length) {
    return [...items];
  }
  const next = [...items];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

/** The reverse-proxy provider row, or `null` on an install that has none. */
export function trustedHeaderProvider(providers: readonly AuthProvider[] | undefined): AuthProvider | null {
  return providers?.find((provider) => provider.kind === TRUSTED_HEADER_KIND) ?? null;
}

/** That provider's rules, winner first (the server already orders them). */
export function rulesOf(mappings: readonly RoleMapping[] | undefined, provider: AuthProvider | null): RoleMapping[] {
  if (!provider) {
    return [];
  }
  return (mappings ?? []).filter(
    (mapping) => mapping.provider === provider.kind && mapping.providerKey === provider.providerKey,
  );
}

/** Strips a leading `@` and case-folds, mirroring `normalize_claim_value`. */
export function normalizeEmailDomain(value: string): string {
  return value.trim().toLowerCase().replace(/^@/, "");
}

/**
 * The domain most of the refused sign-ins came from, as the quick-add's
 * suggestion. Refusals are the only evidence the dashboard has about who is
 * knocking, and they are already loaded for the counter.
 */
export function suggestedEmailDomain(entries: readonly AuditEntry[] | undefined): string {
  const counts = new Map<string, number>();
  for (const entry of entries ?? []) {
    const email = entry.details?.["email"];
    if (typeof email !== "string") {
      continue;
    }
    const domain = normalizeEmailDomain(email.split("@").pop() ?? "");
    if (domain) {
      counts.set(domain, (counts.get(domain) ?? 0) + 1);
    }
  }
  let best = "";
  let bestCount = 0;
  for (const [domain, count] of counts) {
    if (count > bestCount) {
      best = domain;
      bestCount = count;
    }
  }
  return best;
}
