export function shouldExpandAdvancedSettings(search: string, hash: string): boolean {
  const query = search.startsWith("?") ? search.slice(1) : search;
  if (new URLSearchParams(query).get("advanced") === "1") {
    return true;
  }
  return hash === "#firewall";
}
