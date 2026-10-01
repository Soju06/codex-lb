## Why

A native Codex thread received six identical `bridge_previous_response_not_found` responses after an `anchor_abandoned` tombstone was adopted at submit time. The refusal told the client to resend its full conversation, but each retry received the same proxy-injected dead anchor and failed before dispatch, so the documented recovery instruction could not succeed.

## What Changes

- Make a submit-time refusal over an adopted abandonment tombstone retire the rejected proxy-injected anchor from the live session carrier before returning the existing 404 response.
- Allow the next full-resend-shaped request on the same bridge key to keep its complete input and dispatch without that anchor.
- Preserve the abandonment tombstone until a replacement anchor is registered, and continue failing delta-only requests closed.
- Add regression coverage for the first refusal and the next-request recovery boundary, including client-supplied-anchor and crash-safety exclusions.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: require a full resend after a submit-time abandonment-tombstone refusal to escape the rejected proxy-injected anchor while the tombstone continues to protect delta-only continuity.

## Impact

The HTTP Responses bridge submit/retry path and its unit or integration coverage change. The public 404 status, `bridge_previous_response_not_found` code, error message, durable schema, settings and API routes remain unchanged.
