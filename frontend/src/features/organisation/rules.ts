import type { AuditEntry, AuthProvider, RoleMapping } from "@/features/organisation/api";
import type { DashboardUser } from "@/features/access/api";
import type { AccessSummary } from "@/features/auth/schemas";

// Pure helpers behind the Organisation group. They live outside the components
// so the collapse contract, the ordering rules and the refused-sign-in window
// can be unit-tested without rendering anything.

/** The two company sign-in kinds. A `password` row is neither. */
export const TRUSTED_HEADER_KIND = "trusted_header";
export const OIDC_KIND = "oidc";

/** Claim names `app/modules/role_mappings/matching.py` accepts today. */
export const CLAIM_GROUPS = "groups";
export const CLAIM_EMAIL_DOMAIN = "email_domain";

/** The audit window the rules card reports on. */
export const REFUSED_WINDOW_DAYS = 7;
export const REFUSED_ACTION = "login_failed";
export const REFUSED_REASON = "unknown_identity";

/**
 * The server's masking rule, applied to an address the administrator can
 * already see in full. It mirrors `app/core/utils/masking.py::mask_email`
 * character for character on purpose: a refused person is handed this same
 * projection of their own address as a reference, and the point of showing it
 * here is that the two strings match as text. Anything else — a different
 * number of stars, a kept last character — and the reference is unfindable.
 */
export function maskEmail(address: string): string {
  const separator = address.indexOf("@");
  const local = separator < 0 ? address : address.slice(0, separator);
  // Carries its own `@`, so an address with no local part masks to
  // `***@example.com` exactly as `str.partition` leaves it.
  const domain = separator < 0 ? "" : address.slice(separator);
  return `${local.slice(0, 1)}***${domain}`;
}

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

/** The identity-provider row. Seeded on every install, disabled until connected. */
export function oidcProvider(providers: readonly AuthProvider[] | undefined): AuthProvider | null {
  return providers?.find((provider) => provider.kind === OIDC_KIND) ?? null;
}

/**
 * Whether a connection document is stored. The server returns an empty `config`
 * both for a row nobody ever connected and for one whose sealed blob will not
 * open, and it cannot tell them apart either — so the card invites rather than
 * asserting that nothing was ever set up.
 */
export function isConnected(provider: AuthProvider | null): boolean {
  return provider !== null && Object.keys(provider.config).length > 0;
}

/**
 * Whether the turn-on control may be offered: this card started a pre-flight,
 * and the row now carries a stamp strictly newer than the one it saw then.
 *
 * A non-null stamp on its own is never enough. The proof belongs to the admin
 * who earned it and the API deliberately withholds `test_login_user_id`, so a
 * colleague's fresh proof looks identical from here and the server would refuse
 * the enable with 409. `observed === null` is the ordinary case: writing the
 * connection clears the stamp, so the pre-flight starts from nothing.
 */
export function armedByTestLogin(observed: string | null, current: string | null): boolean {
  if (current === null) {
    return false;
  }
  if (observed === null) {
    return true;
  }
  return Date.parse(current) > Date.parse(observed);
}

/**
 * The name to put in the collapsed summary, taken from the public login hint
 * the session already holds — `access_summary` carries provider kinds and never
 * labels, and the collapsed group is not allowed to make a request to find out.
 *
 * Named only when exactly one company sign-in is active: with a proxy and an
 * identity provider both on, "connected" would have to guess which one is meant.
 */
export function companyLoginLabel(providers: readonly { kind: string; label: string }[]): string | null {
  const external = providers.filter((provider) => provider.kind !== "password");
  return external.length === 1 ? (external[0]?.label ?? null) : null;
}

/** The connection fields, in the order the wizard asks for them. */
export const OIDC_CONNECTION_FIELDS = [
  "issuer",
  "discoveryUrl",
  "clientId",
  "clientSecret",
  "redirectUri",
] as const;
export const OIDC_CLAIM_FIELDS = ["subjectClaim", "emailClaim", "nameClaim", "groupsClaim"] as const;

export type OidcField = (typeof OIDC_CONNECTION_FIELDS)[number] | (typeof OIDC_CLAIM_FIELDS)[number];
export type OidcDraft = Record<OidcField, string>;
/** Why a value cannot be sent; each maps to one `organisation.oidc.validation.*` string. */
export type OidcProblem = "required" | "https" | "callback" | "tooLong" | "whitespace";

const OIDC_FIELDS = new Set<string>([...OIDC_CONNECTION_FIELDS, ...OIDC_CLAIM_FIELDS]);
const MAX_URL = 512;
const MAX_FIELD = 256;

/**
 * The field an `invalid_provider_config` refusal names. The server answers in
 * snake case (`discovery_url`) while the body it refused is camel case, so the
 * inline message would otherwise have nothing to attach itself to.
 */
export function oidcFieldFromParam(param: string): OidcField | null {
  const camel = param.replace(/_([a-z])/g, (_match, letter: string) => letter.toUpperCase());
  return OIDC_FIELDS.has(camel) ? (camel as OidcField) : null;
}

function urlProblem(value: string, { required }: { required: boolean }): OidcProblem | null {
  if (value === "") {
    return required ? "required" : null;
  }
  if (value.length > MAX_URL) {
    return "tooLong";
  }
  try {
    return new URL(value).protocol === "https:" ? null : "https";
  } catch {
    return "https";
  }
}

function claimProblem(value: string): OidcProblem | null {
  if (value === "") {
    return null;
  }
  if (value.length > MAX_FIELD) {
    return "tooLong";
  }
  return /\s/.test(value) ? "whitespace" : null;
}

/** A required, length-bounded field, as `OidcConfigRequest` declares one. */
function textProblem(value: string): OidcProblem | null {
  if (value === "") {
    return "required";
  }
  return value.length > MAX_FIELD ? "tooLong" : null;
}

/**
 * Everything `OidcConfigRequest` and `build_oidc_config` would refuse, checked
 * before the write. Pydantic-level rejections (a missing secret, an over-long
 * field) come back as `validation_error` with no `param` at all, so a refusal
 * the wizard let through could not be shown on the field that caused it.
 *
 * It judges the values the request will carry, not the ones still in the boxes,
 * so every field `configFrom` trims is trimmed here first. That cuts both ways
 * and both are wrong without it: a client id of nothing but spaces would be
 * sent as the empty string the request model refuses unattributably, and a
 * claim name typed with a stray space around it would be refused inline
 * although the server would have taken it. `clientSecret` is the one field left
 * alone, because it is the one field sent verbatim — trimming a credential
 * decides on the operator's behalf what their secret is.
 *
 * Host rules — loopback, link-local, the metadata endpoint — stay on the
 * server: they are the reason the pre-flight exists and duplicating the address
 * arithmetic here would only produce two answers to disagree about.
 */
export function validateOidcDraft(
  draft: OidcDraft,
  { callbackPath }: { callbackPath: string },
): Partial<Record<OidcField, OidcProblem>> {
  const problems: Partial<Record<OidcField, OidcProblem>> = {};
  const set = (field: OidcField, problem: OidcProblem | null) => {
    if (problem) {
      problems[field] = problem;
    }
  };
  const sent = (field: Exclude<OidcField, "clientSecret">) => draft[field].trim();
  set("issuer", urlProblem(sent("issuer"), { required: true }));
  set("discoveryUrl", urlProblem(sent("discoveryUrl"), { required: false }));
  set("clientId", textProblem(sent("clientId")));
  set("clientSecret", textProblem(draft.clientSecret));
  const redirectUri = sent("redirectUri");
  const redirect = urlProblem(redirectUri, { required: true });
  if (redirect) {
    set("redirectUri", redirect);
  } else if (new URL(redirectUri).pathname !== callbackPath) {
    set("redirectUri", "callback");
  }
  for (const field of OIDC_CLAIM_FIELDS) {
    set(field, claimProblem(sent(field)));
  }
  return problems;
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
