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
