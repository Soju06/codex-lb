## Why

The Astra configuration-update policy rejects a valid anchored continuation
when the client sends `truncation: "auto"`, even though subscription
serialization removes that unsupported upstream field. It also rejects
logprobs controls on every Astra request, including requests with no
configuration update or API key. Neither rejection belongs to the
configuration-update policy.

## What Changes

- Permit `truncation: "auto"` alongside valid Astra updates; preserve the
  existing stripping of that field at the subscription boundary.
- Remove Astra-specific logprobs capability rejection without changing
  generic Responses validation or upstream capability decisions.
- Preserve the enforced continuation reset, update schema and ordering, and
  automatic compaction rejection.

## Impact

The Responses HTTP routes and their subscription transport policy change.
The previously archived Astra policy remains the baseline; this correction
does not decide whether the injected enforced-effort reset should remain.
