## Context

The existing usage-refresh policy allowed any snapshot with `credits_has = true` to override an exhausted secondary or weekly window. A live Team account showed `secondary_window.used_percent = 100`, `credits.has_credits = true`, `credits.balance = null`, and `credits.unlimited = false`; codex-lb displayed it as `active` and the relative-availability balancer continued to select it with `remaining_credits=0.00`.

## Goals / Non-Goals

**Goals:**

- Make the credit override require spendable-credit evidence.
- Share the same rule between quota derivation, account/dashboard mapper status derivation, and proxy account-state derivation.
- Keep positive-balance and unlimited-credit recovery working.

**Non-Goals:**

- Do not change usage payload parsing unless upstream sends a numeric balance that codex-lb drops.
- Do not reclassify unrelated upstream request-shape errors as quota errors without raw quota evidence.
- Do not add new settings, dashboard controls, migrations, or deploy mechanics.
- Do not change the account-summary rate-limited recovery trust gate (`mappers._has_credit_override`); it stays on its existing predicate as a separate concern.
- Do not change primary/secondary precedence: both windows exhausted without spendable credits stays `quota_exceeded` with the secondary reset, as on `main`.

## Decisions

- Use the quota module as the shared owner for usable-credit semantics. The account mapper and proxy selector delegate to that rule instead of carrying separate interpretations.
- Ignore bare `credits_has = true` for override purposes. It is retained as metadata but is not proof of spendable credits when `credits_balance` is missing or zero.
- Keep foreground selection advisory: the proxy keeps calling `apply_usage_quota(..., infer_status_from_usage=False)` with no new selector guard. The only proxy-visible effect is that a persisted long-window quota block is no longer cleared by a bare `credits_has` flag.

## Risks / Trade-offs

- [Risk] An upstream account might omit balance while still having spendable credits. -> Mitigation: `credits_unlimited = true` still overrides, and a later positive balance recovers the account.
- [Risk] Existing docs/spec text still says bare `credits_has` is usable. -> Mitigation: modify both existing `usage-refresh-policy` requirements that define the credit-backed override.
