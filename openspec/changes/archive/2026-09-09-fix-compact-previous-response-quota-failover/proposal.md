# Fix Compact Previous-Response Quota Failover

## Why

When a long conversation's previous-response owner account is quota-excluded and the next
client action is a compaction, the compact request wedges the session. The compact path pins
selection to the resolved owner (`fallback_on_preferred_account_unavailable` is false whenever
a pin exists) and raises the selection failure straight to the client (`429
usage_limit_reached` / 503, or the owner's in-request 429). Because the pin re-resolves the
same exhausted owner on every retry, the client cannot compact — and cannot shrink its history
to continue — until the owner's quota window resets.

Normal turns already escape exactly this state through account-neutral fresh replay (strip the
stale `previous_response_id` anchor, verify the payload is a self-contained account-neutral
full resend, exclude the dead owner, reselect). This change gives the compact surface the same
selection-time recovery, rescoped per the maintainer review on PR #1490: activation only for
`previous_response_id`-only pins over self-contained histories, reusing the existing
account-neutral fresh-replay gates, gated on quota-caused owner exclusion, with no
continuity-rebind/CAS/fencing machinery.

## What Changes

- When a compact request is pinned **only** by `previous_response_id` (no turn-state owner, no
  input-file owner, no session identity on the request, no session-ownership affinity) and
  account selection cannot return the pinned owner, the proxy attempts account-neutral
  fresh-replay recovery instead of failing: it verifies the anchor-free upstream compact
  payload against the shared account-neutral fresh-replay rules plus the retained-prior-output
  transcript shape, and on success removes `previous_response_id`, strips downstream
  session/turn affinity aliases from upstream-bound headers, excludes the unavailable owner,
  and reselects among the remaining eligible accounts.
- Recovery activates only for quota-caused owner loss: the owner's persisted status is
  `RATE_LIMITED`/`QUOTA_EXCEEDED` at selection time, or the owner was excluded mid-request by
  a pre-visible quota/rate-limit failover. Post-selection authentication, refresh, transport,
  and transient exclusions keep their existing owner-bound surfaces.
- Every request outside the gate keeps today's fail-closed behavior, now also recorded on the
  existing `continuity_fail_closed` observability counter (surface `compact`, reason
  `owner_account_unavailable`).

## Impact

- Affected specs: `responses-api-compat` (one added requirement).
- Affected code: `app/modules/proxy/_service/compact.py` only (recovery branch in the account
  selection loop, a payload-verification helper reusing `app/modules/proxy/replay_safety.py`
  and `app/modules/proxy/continuity.py`, and an owner-status quota check).
- No new settings, endpoints, schemas, durable-bridge methods, or dashboard surfaces.
  Reservation settlement is unchanged: recovery introduces no new terminal raise; existing
  settle-before-raise sites still cover every exit.
