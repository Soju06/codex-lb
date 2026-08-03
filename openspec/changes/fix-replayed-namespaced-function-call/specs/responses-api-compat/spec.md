## ADDED Requirements

### Requirement: Replayed function-call namespace metadata is local-only on upstream input

For standard and compact Responses requests, the proxy MUST omit `namespace` from every replayed `input` item whose `type` is `function_call` before forwarding the request upstream. The proxy MUST preserve all other fields on that item, MUST retain the original namespace metadata for local call-identity and replay-deduplication processing, and MUST NOT alter client-provided top-level tool entries as part of this normalization.

#### Scenario: Standard Responses replay omits function-call namespace upstream

- **WHEN** a standard Responses request replays a `function_call` input item with `namespace`, `call_id`, `name`, and `arguments`
- **THEN** the upstream payload omits only that item's `namespace`
- **AND** preserves its `call_id`, `name`, and `arguments`
- **AND** the local request input retains the namespace metadata

#### Scenario: Compact Responses replay omits function-call namespace upstream

- **WHEN** a compact Responses request replays a `function_call` input item with a namespace
- **THEN** its upstream payload omits the input item's `namespace`
- **AND** preserves the remaining function-call fields

#### Scenario: Top-level namespace tool remains byte-preserved

- **WHEN** the client includes a top-level tool entry whose `type` is `namespace`
- **THEN** standard Responses serialization forwards that tool entry byte-identically
