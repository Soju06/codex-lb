# Change: release-stream-lease-on-early-close

## Why

When the native Responses path gives up on a transport failure, the
`stream_incomplete` raise in `_normalize_public_responses_stream` leaves the
lease-holding generators below it suspended at their `yield`s. Nothing closes
them, so `_stream_with_retry`'s cleanup never runs, the account stream lease is
never released, and the account slot is lost until restart (#2470).

## What Changes

- `_normalize_public_responses_stream` closes its inner stream before raising
  the native transport-failure `stream_incomplete` error.
- `_stream_response_error_events` closes its inner stream in a `finally`, so an
  early close by the consumer reaches the retry generator.
- `_normalize_reasoning_summary_stream` closes its inner stream in a `finally`
  for the same reason.

No request, response, or error contract changes; only the cleanup of the inner
generators on the give-up path.

## Impact

- `app/modules/proxy/api.py`
- `tests/unit/test_proxy_utils.py`
- `openspec/specs/proxy-admission-control/spec.md` (new requirement)
