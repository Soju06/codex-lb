import type { AccountSummary } from "@/features/accounts/schemas";
import { buildDuplicateAccountIdSet, formatCompactAccountId } from "@/utils/account-identifiers";

const MULTI_ACCOUNT_NAMES_INLINE_LIMIT = 3;

export type AccountDisplayEntry = {
  accountId: string;
  primary: string;
  secondary: string | null;
  title: string;
};

export type AccountDisplay = {
  primary: string;
  secondary: string | null;
  title: string;
};

function normalizeText(value: string | null | undefined): string {
  return (value ?? "").trim();
}

function buildTitle(primary: string, secondary: string | null): string {
  return secondary ? `${primary}\n${secondary}` : primary;
}

function buildUnknownAccountDisplay(accountId: string): AccountDisplay {
  const compactId = formatCompactAccountId(accountId, 7, 5);
  const secondary = `ID ${compactId}`;
  return {
    primary: "Unknown account",
    secondary,
    title: `Unknown account\n${accountId}`,
  };
}

export function buildAccountDisplayIndex(accounts: AccountSummary[]): Map<string, AccountDisplayEntry> {
  const duplicateAccountIds = buildDuplicateAccountIdSet(accounts);
  const index = new Map<string, AccountDisplayEntry>();

  for (const account of accounts) {
    const displayName = normalizeText(account.displayName);
    const email = normalizeText(account.email);

    const primary = displayName || email || "Unnamed account";
    const hasDistinctEmail = displayName.length > 0 && email.length > 0 && displayName.toLowerCase() !== email.toLowerCase();
    const secondary = hasDistinctEmail
      ? email
      : (duplicateAccountIds.has(account.accountId) ? `ID ${formatCompactAccountId(account.accountId, 6, 4)}` : null);

    index.set(account.accountId, {
      accountId: account.accountId,
      primary,
      secondary,
      title: buildTitle(primary, secondary),
    });
  }

  return index;
}

export function resolveAccountDisplay(accountId: string | null, displayIndex: Map<string, AccountDisplayEntry>): AccountDisplay {
  if (!accountId) {
    return {
      primary: "No account",
      secondary: null,
      title: "No account",
    };
  }

  const entry = displayIndex.get(accountId);
  if (entry) {
    return {
      primary: entry.primary,
      secondary: entry.secondary,
      title: entry.title,
    };
  }
  return buildUnknownAccountDisplay(accountId);
}

export function formatAccountsSummary(accountIds: string[], displayIndex: Map<string, AccountDisplayEntry>): AccountDisplay {
  if (accountIds.length === 0) {
    return {
      primary: "All accounts",
      secondary: "Uses every available account",
      title: "All accounts",
    };
  }

  if (accountIds.length === 1) {
    return resolveAccountDisplay(accountIds[0] ?? null, displayIndex);
  }

  const resolved = accountIds.map((accountId) => resolveAccountDisplay(accountId, displayIndex));
  const names = resolved.map((entry) => entry.primary);

  if (accountIds.length <= MULTI_ACCOUNT_NAMES_INLINE_LIMIT) {
    return {
      primary: names.join(", "),
      secondary: `${accountIds.length} selected`,
      title: resolved.map((entry) => entry.title).join("\n"),
    };
  }

  const preview = names.slice(0, 2).join(", ");
  const remaining = accountIds.length - 2;
  return {
    primary: `${accountIds.length} accounts`,
    secondary: `${preview} +${remaining} more`,
    title: resolved.map((entry) => entry.title).join("\n"),
  };
}
