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

export function shouldExpandOrganisationSettings(hash: string): boolean {
  return (
    hash === ORGANISATION_HASH ||
    hash === ORGANISATION_REFUSED_HASH ||
    hash === ORGANISATION_LOGIN_POLICY_HASH
  );
}
