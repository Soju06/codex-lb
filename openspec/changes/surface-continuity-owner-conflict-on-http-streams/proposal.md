# Surface continuity owner conflicts on direct HTTP streams

## Why

When required continuity-owner selection fails on a direct HTTP (SSE) stream
because independently resolved hard continuity sources identify different
accounts, the stream's fail-closed path previously emitted the generic
`previous_response_owner_unavailable` error envelope. Clients, telemetry, and
request logs therefore could not distinguish a retryable owner-conflict turn
from an unavailable owner, and the conflict reason produced by selection was
discarded. Live usage snapshot settlement also resolves upstream ChatGPT
account identities per snapshot without an index on
`accounts.chatgpt_account_id`.

## What Changes

- Direct HTTP stream fail-closed paths surface the selection's
  `continuity_owner_conflict` error code and message in the SSE
  `response.failed` envelope instead of mislabeling the failure as
  `previous_response_owner_unavailable`.
- The continuity fail-closed telemetry for those paths records reason
  `owner_conflict` and propagates the selection error code as the upstream
  error code; the persisted request log records the surfaced conflict code.
- Ordinary owner-unavailability failures keep the existing
  `previous_response_owner_unavailable` envelope, reason, and upstream codes.
- Add an `idx_accounts_chatgpt_account_id` index on
  `accounts (chatgpt_account_id)` so live usage settlement's per-snapshot
  ChatGPT identity lookups are index-supported; the PostgreSQL migration
  builds it concurrently and repairs an invalid leftover from an interrupted
  concurrent build instead of accepting it via `IF NOT EXISTS`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `sticky-session-operations`: direct HTTP stream fail-closed reporting
  distinguishes continuity owner conflicts from owner unavailability.
- `database-backends`: account ChatGPT identity lookups are index-supported.

## Impact

- Affected code: direct HTTP stream fail-closed error surfacing in
  `app/modules/proxy/_service/streaming/retry.py`, compact turn-state conflict
  message wording, `accounts` index metadata, and one additive migration.
- Affected API behavior: the SSE error envelope for a continuity conflict on a
  direct stream now reports `continuity_owner_conflict`; non-conflict owner
  failures are unchanged.
- No setting, dependency, dashboard, or public request field is added.
