# Background JSON completion context

## Purpose

Distinguish completion of a non-streaming HTTP exchange from completion of the
background Responses job represented by its JSON body.

## Decision

For a request whose upstream `stream` value is exactly `false`, one fully read
canonical background acknowledgement completes the transport and settles
successfully. The acknowledgement has `object: "response"`, a non-empty `id`, a
queued/in-progress status matching its event type, and an empty `output` list.
The object is returned unchanged. The event names remain nonterminal for SSE
streams.

## Constraints

- Do not add polling or wait for background job completion.
- Do not reinterpret failed, incomplete, malformed, or empty responses.
- Do not treat partial or malformed queued/in-progress objects as accepted
  acknowledgements.
- Do not relax genuine `stream_incomplete` handling for streaming requests.
- Preserve account selection, reservation, usage, and response shapes.

## Example

A `stream: false` request receives HTTP 200 JSON with
`{"object":"response","id":"resp_1","status":"queued","output":[]}`. The client
receives that object, the request log records success keyed by `resp_1`, and
account health records success. A queued object that omits `object` or a
non-empty `id` returns the same external contract error as other malformed
acknowledgements, and a `stream: true` SSE connection that disconnects after
`response.queued` retains error settlement.
