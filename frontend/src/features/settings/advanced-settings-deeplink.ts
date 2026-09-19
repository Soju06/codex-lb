export function shouldExpandAdvancedSettings(search: string, hash: string): boolean {
  const query = search.startsWith("?") ? search.slice(1) : search;
  if (new URLSearchParams(query).get("advanced") === "1") {
    return true;
  }
  return hash === "#firewall";
}

// Access card deep links: `/settings#access` opens the card on the signed-in
// person's own controls, `/settings#access-people` opens the People tab. The
// TOTP card's own anchor (`#totp`) sits inside those controls, so it selects
// the same tab.
export const ACCESS_CARD_ID = "access";
export const ACCESS_PEOPLE_HASH = "#access-people";
export const ACCESS_HASH = `#${ACCESS_CARD_ID}`;
const MY_SIGN_IN_HASHES = new Set([ACCESS_HASH, "#totp"]);

export type AccessTab = "people" | "my-sign-in";

export function accessTabFromHash(hash: string): AccessTab | null {
  if (hash === ACCESS_PEOPLE_HASH) {
    return "people";
  }
  return MY_SIGN_IN_HASHES.has(hash) ? "my-sign-in" : null;
}

// Organisation group deep links: `/settings#organisation` expands the group,
// `/settings#organisation-refused` expands it and opens the refused sign-ins
// of the last seven days (the audit log filtered on `login_failed` /
// `unknown_identity`). Both are hashes so the link works from anywhere.
export const ORGANISATION_GROUP_ID = "organisation";
export const ORGANISATION_HASH = `#${ORGANISATION_GROUP_ID}`;
export const ORGANISATION_REFUSED_HASH = "#organisation-refused";
/** The login-policy card's own anchor: the id is on the card's `<section>`. */
export const ORGANISATION_LOGIN_POLICY_ID = "organisation-login-policy";
export const ORGANISATION_LOGIN_POLICY_HASH = `#${ORGANISATION_LOGIN_POLICY_ID}`;
/**
 * The company sign-in card's anchor. The id and the query flag are not this
 * module's choice: the OIDC callback already redirects a completed pre-flight
 * to `OIDC_SETTINGS_PATH` (`app/modules/dashboard_auth/oidc_api.py`), so the
 * frontend has to answer to that URL or the return lands on a collapsed group.
 */
export const ORGANISATION_OIDC_ID = "oidc";
export const ORGANISATION_OIDC_HASH = `#${ORGANISATION_OIDC_ID}`;
/** The automatic account management card's own anchor, for pointing somebody at it. */
export const ORGANISATION_SCIM_ID = "organisation-automatic-accounts";
export const ORGANISATION_SCIM_HASH = `#${ORGANISATION_SCIM_ID}`;
const ORGANISATION_QUERY_FLAG = "org";
export const ORGANISATION_SETTINGS_RETURN_URL = `/settings?${ORGANISATION_QUERY_FLAG}=1${ORGANISATION_OIDC_HASH}`;

const ORGANISATION_HASHES = new Set([
  ORGANISATION_HASH,
  ORGANISATION_REFUSED_HASH,
  ORGANISATION_LOGIN_POLICY_HASH,
  ORGANISATION_OIDC_HASH,
  ORGANISATION_SCIM_HASH,
]);

export function shouldExpandOrganisationSettings(search: string, hash: string): boolean {
  const query = search.startsWith("?") ? search.slice(1) : search;
  if (new URLSearchParams(query).get(ORGANISATION_QUERY_FLAG) === "1") {
    return true;
  }
  return ORGANISATION_HASHES.has(hash);
}
