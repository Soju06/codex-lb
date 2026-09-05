# Context: perf-bridge-installation-id-stamp

## Purpose

Pure performance refactor of the account installation-id stamp on the HTTP
bridge and WebSocket relay paths. Output bytes are unchanged, so the governing
requirements in `openspec/specs/upstream-proxy-routing/spec.md` ("Codex
installation metadata must be account-owned") and
`openspec/specs/responses-api-compat/spec.md` ("Selected Codex installation
identity is internally consistent") need no delta.

## Decisions

- **Memo keyed on (installation id, `str` identity), not on content.** The
  submit path re-stamps the same text at four method-body-level sites and the
  retry paths re-stamp again. Every transformation that can change the frame
  (`_text_without_operation_id`, `_text_with_previous_response_id`,
  `_text_with_operation_id`, image inlining, slimming) returns a new `str`
  object, and `replace_connection` changes the account and therefore the id, so
  identity plus id is a sufficient and allocation-free cache key. The memo is
  overwritten on every slow-path call with exactly the objects that call
  returned, so a hit always means "the previous call under this id returned
  this object".
- **Splice instead of a recorded span.** The frame is our own compact
  `json.dumps` output; compact encoding is compositional, so rewriting the
  trailing `,"client_metadata":{...}}` segment (or appending one when the key
  is absent) yields the same bytes as decode/rewrite/encode. Recording a span or
  tail on the request state was rejected because fresh texts come from
  discarded prepare states, and moving `client_metadata` to the last key was
  rejected because it would change `durable_bridge_operation_fingerprint`
  values persisted in `HttpBridgeOperationRecord.request_fingerprint`.
- **Fallback stays the reference implementation.** Body-carried
  `client_metadata` that precedes `type`, Responses-Lite frames where the
  finalizer appends `reasoning` after `client_metadata`, nested keys, a leading
  `client_metadata` key and non-object frames all take the original
  decode/encode path.

## Constraints and failure modes

- The splice presumes a compact, canonical frame (no whitespace, insertion
  order). All bridge and relay texts are produced in-process by that encoder;
  for a non-canonical frame the splice still stamps correctly but would leave
  the untouched prefix un-normalised where the old path would have re-encoded
  it.
- A hit skips the deterministic size re-check on an unchanged fresh text; the
  check still runs once for every distinct stamped object.
- Retry sites that pass a different session or text object miss and re-stamp.

## Example

A 216 KB Codex CLI frame with `client_metadata` as the last key: the four
sequential stamps previously cost ~2.7 ms x 4 of decode/encode (x86); with this
change the first stamp costs ~0.02 ms and the remaining three are identity
checks. API-key/SDK traffic without `client_metadata` takes the insertion form
(~0.16 ms, dominated by the substring scan and the single copy).
