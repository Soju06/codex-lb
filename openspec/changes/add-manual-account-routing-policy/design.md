## Context

Manual account routing policy was originally applied inside only part of the
core strategy dispatch. `single_account`, drain strategies, service-level
preferred-account narrowing, and sticky-session reuse could therefore select a
normal account while eligible `burn_first` capacity remained. Additional-quota
overrides further require the decision to use the request-effective policy on
`AccountState`, not only the persisted policy on `Account`.

The correction crosses the pure balancer, the stateful load balancer, proxy
selection orchestration, and strict continuity checks. Hard eligibility still
runs before policy precedence.

## Goals / Non-Goals

**Goals:**

- Make the highest-priority nonempty policy pool the input to every routing
  strategy and selection preference.
- Let an eligible `burn_first` account override `single_account`, health and
  budget preference, sticky mappings, preferred accounts, and owner-derived
  affinity.
- Preserve effective additional-quota policy through selection metadata so
  downstream continuity checks can distinguish an intentional burn override.
- Preserve existing fallback behavior when no eligible burn-first account is
  available.

**Non-Goals:**

- Do not bypass authorization, API-key scope, model-plan, quota, cooldown,
  explicit retry exclusion, or concurrency-cap eligibility.
- Do not add a new persisted field or migration.
- Do not make account-local upstream identifiers portable across accounts.

## Decisions

1. **Apply policy precedence in the pure selector before strategy and health
   ranking.** This gives every strategy one consistent candidate-pool contract.
   Per-strategy burn checks were rejected because they recreate the original
   bypass risk whenever a strategy is added.

2. **Evaluate budget and preferred-account behavior against the full eligible
   state list.** The stateful load balancer first probes the full list so an
   eligible burn account wins, then honors a preferred account only within the
   same effective policy tier. Service-level singleton queries were rejected
   because they hide burn candidates before policy can run.

3. **Treat configured `single_account` as a fallback target.** The selector
   receives `single_account_id` explicitly. An eligible burn pool wins first;
   otherwise only the configured account may serve the request, preserving the
   existing failure contract.

4. **Carry effective policy on `AccountSelection`.** Downstream strict-owner
   checks accept an account mismatch only when selection reports
   `routing_policy = burn_first`. Reading `Account.routing_policy` was rejected
   because additional-quota overrides can change policy for one request without
   changing the persisted account row.

5. **Rebind mutable sticky mappings immediately.** A healthy normal pin no
   longer blocks an eligible burn account. If no burn target is selectable, the
   existing budget, rate-limit grace, and sticky fallback behavior remains.

## Risks / Trade-offs

- **[Risk] Account-local file or previous-response references may be rejected on
  the burn account.** → Preserve the upstream error and retry machinery, never
  silently fall back to a lower policy tier while burn capacity remains, and
  document the operational consequence.
- **[Risk] Wider candidate loading adds selection work compared with singleton
  queries.** → Reuse the existing cached selection-input snapshot and keep all
  policy/preference probes in memory.
- **[Risk] A burn account in a degraded health tier now precedes a healthy normal
  account.** → This is intentional operator policy; cooldown, backoff, and hard
  availability filters still remove accounts that cannot safely serve traffic.
- **[Risk] New selection metadata could drift from additional-quota overrides.**
  → Populate it from the selected `AccountState`, which already carries the
  request-effective policy.

## Migration Plan

1. Deploy the selector and proxy orchestration changes together.
2. Confirm routing metrics and logs show burn accounts receiving new selections
   across every configured strategy.
3. Monitor invalid file/previous-response reference errors for workloads with
   owner-derived affinity.
4. Roll back the code change to restore the former affinity and single-account
   precedence; no database rollback is required.

## Open Questions

None. Product direction explicitly requires `burn_first` to override every
routing strategy and affinity preference after hard eligibility.
