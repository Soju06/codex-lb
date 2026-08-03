## ADDED Requirements

### Requirement: Replayed tool-call namespace metadata is local-only on upstream input

For standard and compact Responses requests, the proxy MUST omit `namespace` from every replayed `input` item whose `type` is `function_call`, `custom_tool_call`, or `apply_patch_call` before forwarding the request upstream. The proxy MUST preserve all other fields on that item, MUST retain the original namespace metadata for local call-identity and replay-deduplication processing, and MUST NOT alter client-provided top-level tool entries as part of this normalization.

#### Scenario: Standard Responses replay omits tool-call namespaces upstream

- **WHEN** a standard Responses request replays `function_call` and `custom_tool_call` input items with `namespace`
- **THEN** the upstream payload omits only those items' `namespace`
- **AND** preserves their remaining call fields
- **AND** the local request input retains the namespace metadata

#### Scenario: Compact Responses replay omits tool-call namespace upstream

- **WHEN** a compact Responses request replays a recognized tool-call input item with a namespace
- **THEN** its upstream payload omits the input item's `namespace`
- **AND** preserves the remaining tool-call fields

#### Scenario: WebSocket response.create omits tool-call namespaces upstream

- **WHEN** a Responses WebSocket request replays namespaced `function_call` and `custom_tool_call` input items
- **THEN** the upstream `response.create` frame omits only those items' `namespace`
- **AND** preserves their remaining call fields

#### Scenario: Top-level namespace tool remains byte-preserved

- **WHEN** the client includes a top-level tool entry whose `type` is `namespace`
- **THEN** standard Responses serialization forwards that tool entry byte-identically
