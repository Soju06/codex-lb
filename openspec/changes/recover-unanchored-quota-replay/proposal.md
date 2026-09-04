## Why

A long Codex conversation can resend its local transcript without a
`previous_response_id` while retaining response-owned reasoning ciphertext and item ids. If
the selected account rejects that request for exhausted quota before any downstream event,
the raw streaming path currently records that account as the payload owner. Reselection then
requires the now-excluded account and surfaces its `usage_limit_reached` response even when
another pool account is healthy.

## What Changes

- Recognize a pre-visible quota rejection as a safe failover point for an unanchored,
  self-contained full resend.
- Project response-owned bookkeeping out of the replay input, require retained assistant
  output followed by fresh user input, and re-run the existing account-neutral replay gates.
- Clear only the soft dispatch owner and reallocate prompt-cache affinity before selecting a
  different account.
- Keep previous-response, conversation, turn-state, file, single-account, incomplete-history,
  non-neutral, and post-visible requests fail-closed.

## Impact

The change is limited to raw Responses streaming failover and route-level regression tests.
It adds no setting, schema, credential migration, or durable-session mutation.
