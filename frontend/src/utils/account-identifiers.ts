export type AccountIdentityLike = {
  accountId: string;
  email: string;
  displayName: string;
};

function identityKey(account: AccountIdentityLike): string {
  const email = account.email.trim().toLowerCase();
  if (email) {
    return `email:${email}`;
  }
  const displayName = account.displayName.trim().toLowerCase();
  if (displayName) {
    return `display:${displayName}`;
  }
  return `id:${account.accountId}`;
}

export function buildDuplicateAccountIdSet<T extends AccountIdentityLike>(accounts: T[]): Set<string> {
  const counts = new Map<string, number>();
  for (const account of accounts) {
    const key = identityKey(account);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }

  const duplicateAccountIds = new Set<string>();
  for (const account of accounts) {
    if ((counts.get(identityKey(account)) ?? 0) > 1) {
      duplicateAccountIds.add(account.accountId);
    }
  }
  return duplicateAccountIds;
}

export function formatCompactAccountId(accountId: string): string {
  if (accountId.length <= 18) {
    return accountId;
  }
  return `${accountId.slice(0, 8)}...${accountId.slice(-6)}`;
}
