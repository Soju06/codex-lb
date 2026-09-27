## Why

Fresh Codex 0.157.1 conversations receive 409 before reaching a custom endpoint because client-generated messages include timestamp and content-kind metadata that the source portability validator does not recognize. This also affects client tool results and delegated messages using the same envelope.

## What Changes

- Validate the observed client metadata shape for direct-source ownership extraction and portability.
- Preserve metadata and input bodies on the wire; retain all upstream ownership boundaries.
- Cover fresh conversations, continuations across replicas, malformed metadata and subscription isolation.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-source-routing`: direct-source client bookkeeping metadata compatibility.

## Impact

Direct HTTP Responses classification only. No database migration, new setting, credential change or subscription replay change.
