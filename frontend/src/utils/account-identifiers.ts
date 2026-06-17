export function formatCompactAccountId(accountId: string, headChars = 8, tailChars = 6): string {
  const head = Math.max(1, headChars);
  const tail = Math.max(1, tailChars);
  if (accountId.length <= head + tail + 3) {
    return accountId;
  }
  return `${accountId.slice(0, head)}...${accountId.slice(-tail)}`;
}
