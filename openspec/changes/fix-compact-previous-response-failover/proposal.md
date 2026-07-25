# Fix Compact Previous-Response Failover

## Why

When a long conversation's previous-response owner account runs out of quota mid-session
and the next client action is a compaction, the compact request wedges the session. The
compact path pins selection to the resolved owner (`fallback_on_preferred_account_unavailable`
is false whenever a pin exists) and raises the selection failure straight to the client
(503 `no_accounts` / `hard_affinity_saturated`, or the owner's earlier 429). Because the
pin re-resolves the same exhausted owner on every retry, the client cannot compact — and
cannot shrink its history to continue — until the owner's quota window resets.

Normal turns already recover from exactly this state through account-neutral fresh-replay
(strip the stale `previous_response_id` anchor, verify the payload is a self-contained
account-neutral full resend, exclude the dead owner, reselect). The compact surface is a
strictly easier case — the compact payload is required to carry the full conversation
history in `input` and the response is never streamed — yet it has no recovery path.

## What Changes

- When a compact request is pinned **only** by `previous_response_id` (no client-supplied
  turn-state owner, no input-file owner) and account selection cannot return the pinned
  owner (including after the owner is excluded by an in-request pre-visible quota/rate-limit
  failover), the proxy attempts account-neutral fresh-replay recovery instead of failing:
  it verifies the anchor-free upstream compact payload against the shared account-neutral
  fresh-replay rules, and on success removes `previous_response_id`, strips downstream
  session/turn affinity aliases from upstream-bound headers, clears sticky affinity for the
  retry, excludes the unavailable owner, and reselects among the remaining eligible accounts.
- Payloads that fail account-neutral verification (encrypted compaction state, server-assigned
  item ids, account-scoped file handles, hosted/MCP state, conversation/prompt handles, …)
  keep today's fail-closed behavior, now also recorded on the existing
  `continuity_fail_closed` observability counter (surface `compact`, reason
  `owner_account_unavailable`).
- Turn-state-pinned and file-pinned compacts remain fail-closed/owner-bound. An unresolvable
  previous-response owner (lookup miss) remains fail-closed. Post-selection refresh,
  authentication, transport, and timeout failures on the pinned owner keep their existing
  owner-bound handling.

## Impact

- Affected specs: `responses-api-compat` (one added requirement).
- Affected code: `app/modules/proxy/_service/compact.py` (recovery branch in the account
  selection loop plus a payload-verification helper reusing `app/modules/proxy/replay_safety.py`
  and `app/modules/proxy/continuity.py`).
- No new settings, endpoints, schemas, or dashboard surfaces. Recovery is zero-config and
  only activates where the request previously failed.
- Reservation settlement is unchanged: recovery introduces no new terminal raise; existing
  settle-before-raise sites still cover every exit.
