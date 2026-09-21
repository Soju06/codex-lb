import type { QueryClient } from "@tanstack/react-query";

import type { AccountSummary } from "@/features/accounts/schemas";
import type { DashboardOverview } from "@/features/dashboard/schemas";

type ResetState = {
  generation: number;
  pending: boolean;
  previousFetchedAt: string | null;
  summary: AccountSummary | null;
};
type ClientState = { generation: number; accounts: Map<string, ResetState>; requests: Map<string, string> };
const clients = new WeakMap<QueryClient, ClientState>();

function stateFor(client: QueryClient): ClientState {
  let state = clients.get(client);
  if (!state) {
    state = { generation: 0, accounts: new Map(), requests: new Map() };
    clients.set(client, state);
  }
  return state;
}

export function resetReconciliationGeneration(client: QueryClient): number {
  return stateFor(client).generation;
}

export function forgetResetReconciliation(client: QueryClient, accountId: string) {
  stateFor(client).accounts.delete(accountId);
  stateFor(client).requests.delete(accountId);
}

export function resetRedeemRequestId(client: QueryClient, accountId: string, supplied?: string): string {
  const requests = stateFor(client).requests;
  const requestId = supplied ?? requests.get(accountId)
    ?? globalThis.crypto?.randomUUID?.() ?? `dashboard-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  requests.set(accountId, requestId);
  return requestId;
}

export function finishResetRedeemRequest(client: QueryClient, accountId: string) {
  stateFor(client).requests.delete(accountId);
}

function mergeOverview(old: DashboardOverview, summary: AccountSummary): DashboardOverview {
  const next = {
    ...old,
    accounts: old.accounts.map((account) => account.accountId === summary.accountId ? summary : account),
    windows: { ...old.windows },
    summary: { ...old.summary },
    weeklyCreditPace: null,
  };
  for (const key of ["primary", "secondary"] as const) {
    const window = old.windows[key];
    const totalKey = key === "primary" ? "primaryWindow" : "secondaryWindow";
    const total = old.summary[totalKey];
    const remaining = key === "primary" ? summary.remainingCreditsPrimary : summary.remainingCreditsSecondary;
    const capacity = key === "primary" ? summary.capacityCreditsPrimary : summary.capacityCreditsSecondary;
    if (!window || !total || remaining == null || capacity == null) continue;
    const accounts = window.accounts.map((entry) => entry.accountId === summary.accountId ? {
      ...entry, remainingCredits: remaining, capacityCredits: capacity,
      remainingPercentAvg: key === "primary"
        ? summary.usage?.primaryRemainingPercent ?? null
        : summary.usage?.secondaryRemainingPercent ?? null,
    } : entry);
    next.windows[key] = { ...window, accounts };
    // Backend aggregate capacity excludes accounts without a usage sample.
    const eligible = accounts.filter((entry) => entry.remainingPercentAvg != null);
    const capacityCredits = eligible.reduce((sum, entry) => sum + entry.capacityCredits, 0);
    const remainingCredits = eligible.reduce((sum, entry) => sum + entry.remainingCredits, 0);
    next.summary[totalKey] = {
      ...total, capacityCredits, remainingCredits,
      remainingPercent: capacityCredits > 0 ? 100 * remainingCredits / capacityCredits : 0,
    };
  }
  return next;
}

function reconcileAccount(client: QueryClient, account: AccountSummary, generation: number): AccountSummary {
  const state = stateFor(client).accounts.get(account.accountId);
  if (!state) return account;
  const fetchedAt = account.resetCreditFetchedAt;
  const latest = state.summary?.resetCreditFetchedAt;
  // A poll started before a reset/targeted merge cannot overwrite its result.
  if (generation < state.generation) {
    return { ...(state.summary ?? account), resetCreditRefreshPending: state.pending };
  }
  // Fresh polls remain authoritative for health, policy, usage and identity.
  // A deliberately absent snapshot on a paused/ineligible account must not
  // restore the pre-pause account merely because its reset timestamp is null.
  if (["paused", "reauth_required", "deactivated"].includes(account.status)) {
    state.pending = false;
  }
  if (state.pending && fetchedAt && (!state.previousFetchedAt || Date.parse(fetchedAt) > Date.parse(state.previousFetchedAt))) {
    state.pending = false;
  }
  const reconciled = { ...account, resetCreditRefreshPending: state.pending };
  if (latest && fetchedAt && Date.parse(fetchedAt) < Date.parse(latest) && state.summary) {
    reconciled.availableResetCredits = state.summary.availableResetCredits;
    reconciled.resetCreditFetchedAt = state.summary.resetCreditFetchedAt;
    reconciled.resetCreditNearestExpiresAt = state.summary.resetCreditNearestExpiresAt;
  }
  state.summary = reconciled;
  return reconciled;
}

export function reconcileAccountsQuery<T extends { accounts: AccountSummary[] }>(
  client: QueryClient, data: T, generation: number,
): T {
  return { ...data, accounts: data.accounts.map((account) => reconcileAccount(client, account, generation)) };
}

export function reconcileDashboardQuery(client: QueryClient, data: DashboardOverview, generation: number): DashboardOverview {
  return data.accounts.reduce((overview, account) => {
    const reconciled = reconcileAccount(client, account, generation);
    if (reconciled === account) return overview;
    if (reconciled.usage === account.usage
      && reconciled.remainingCreditsPrimary === account.remainingCreditsPrimary
      && reconciled.remainingCreditsSecondary === account.remainingCreditsSecondary) {
      return { ...overview, accounts: overview.accounts.map((entry) => entry.accountId === account.accountId ? reconciled : entry) };
    }
    return mergeOverview(overview, reconciled);
  }, data);
}

export function mergeResetAccount(client: QueryClient, accountId: string, summary: AccountSummary | null, pending: boolean) {
  const clientState = stateFor(client);
  const previous = clientState.accounts.get(accountId);
  const cached = client.getQueriesData<{ accounts: AccountSummary[] }>({
    predicate: (query) => (query.queryKey[0] === "accounts" && query.queryKey[1] === "list")
      || (query.queryKey[0] === "dashboard" && query.queryKey[1] === "overview"),
  }).flatMap(([, data]) => data?.accounts ?? []).find((account) => account.accountId === accountId);
  clientState.accounts.set(accountId, {
    generation: ++clientState.generation,
    pending,
    previousFetchedAt: pending ? cached?.resetCreditFetchedAt ?? null : previous?.previousFetchedAt ?? null,
    summary: summary ?? cached ?? null,
  });
  const updated = (account: AccountSummary) => account.accountId === accountId
    ? { ...account, ...(summary ?? {}), resetCreditRefreshPending: pending } : account;
  client.setQueriesData<{ accounts: AccountSummary[] }>({ queryKey: ["accounts", "list"] }, (old) => old
    ? { ...old, accounts: old.accounts.map(updated) } : old);
  client.setQueriesData<DashboardOverview>({ queryKey: ["dashboard", "overview"] }, (old) => {
    if (!old) return old;
    if (!summary) return { ...old, accounts: old.accounts.map(updated) };
    return mergeOverview(old, { ...summary, resetCreditRefreshPending: pending });
  });
}
