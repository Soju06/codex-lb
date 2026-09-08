# Close non-stream Chat Completions collect

## Purpose

Keep non-streaming `/v1/chat/completions` on the same settlement and error-status
contract as `/v1/responses` collect.

## Decision

Hold a reference to the `stream_responses` generator and `_aclose_stream` it in
`finally` after collect. The startup probe already closes the generator when it
sees an error event; this covers the probe-timeout path that then splits the
stream with `__anext__` + `_prepend_first`.

Reuse `_mask_previous_response_not_found_error` so status mapping cannot drift
from Responses.

## Failure mode

If collect returns on the prepended first `response.failed` event, `_prepend_first`
never enters `async for` on the live generator, so Python does not aclose it.
Reservation release lives in that generator's `finally`.

## Example

Client: `POST /v1/chat/completions` `{ "model": "gpt-5.2", "messages": [...], "stream": false }`
after the 2s startup probe timed out. First event is `response.failed` /
`rate_limit_exceeded`. Response is `429` and the reservation row is `released`.
