# Context

## Scope and rationale

This is the residual extraction from the broad stale-anchor hardening work. The
canonical Codex-native classifier and the HTTP bridge recovery transaction are
separate concerns. This change owns only the shared error-shape seam needed by
those vehicles: parsing must remember whether `param` was absent or malformed,
while public boundaries must never echo malformed metadata.

## Decision

Use a small immutable `OpenAIErrorParam` value object rather than a second model
field. Pydantic continues to expose the normal string `param` field for valid
payloads; the private state carries explicit presence and raw JSON only when a
caller needs to distinguish malformed input from absence. Recovery code consumes
the strict classifier, while masking code consumes the public-shape classifier.

## Failure modes

- A present `null`, number, object, array, blank string, or whitespace-only
  string is not a proof that the upstream anchor was rejected safely. It is
  therefore masked when appropriate but cannot trigger replay.
- A typeless payload with an `error` object is terminal for stream settlement,
  but must retain its original error details unless public masking requires a
  sanitized envelope.
- A nested `response.failed` error may be masked without dropping the outer
  response id used by clients to correlate the terminal event.
- The WebSocket and HTTP Responses serializers retain their native event
  envelopes; the Chat Completions adapter deliberately emits its own
  `{"error": ...}` wire shape and only carries over the sanitized error detail.

## Operational notes

The change is zero-config and has no migration or runtime setting. It is safe to
roll out independently of the later HTTP bridge recovery PR. Local proof covers
the unit parser/classifier, Responses public normalizer, and Chat Completions
adapter; hosted CI and maintainer review remain required before merge.
