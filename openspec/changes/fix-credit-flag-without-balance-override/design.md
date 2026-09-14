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

## Decisions

- Use the quota module as the shared owner for usable-credit semantics. The account mapper and proxy selector delegate to that rule instead of carrying separate interpretations.
- Ignore bare `credits_has = true` for override purposes. It is retained as metadata but is not proof of spendable credits when `credits_balance` is missing or zero.
- Keep the selector guard narrow: existing advisory long-window behavior remains unchanged in foreground selection. Explicit non-spendable credit metadata only prevents a persisted long-window quota block from being cleared.

## Risks / Trade-offs

- [Risk] An upstream account might omit balance while still having spendable credits. -> Mitigation: `credits_unlimited = true` still overrides, and a later positive balance recovers the account.
- [Risk] Existing docs/spec text still says bare `credits_has` is usable. -> Mitigation: modify both existing `usage-refresh-policy` requirements that define the credit-backed override.
