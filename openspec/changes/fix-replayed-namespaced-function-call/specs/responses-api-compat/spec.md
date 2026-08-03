## ADDED Requirements

### Requirement: Replayed tool-call namespace metadata is local-only on upstream input

For standard and compact Responses requests, the proxy MUST omit `namespace` from every replayed `input` item whose `type` is `function_call`, `custom_tool_call`, or `apply_patch_call` before forwarding the request upstream. The proxy MUST preserve all other fields on that item, MUST retain the original namespace metadata for local call-identity and replay-deduplication processing, and MUST NOT alter client-provided top-level tool entries as part of this normalization.

#### Scenario: Standard Responses replay omits tool-call namespaces upstream

- **WHEN** a standard Responses request replays `function_call` and `custom_tool_call` input items with `namespace`
- **THEN** the upstream payload omits only those items' `namespace`
- **AND** preserves their remaining call fields
- **AND** the local request input retains the namespace metadata

#### Scenario: Compact Responses replay omits tool-call namespace upstream

- **WHEN** `/v1/responses/compact` replays a recognized tool-call input item with a namespace
- **THEN** its upstream payload omits the input item's `namespace`
- **AND** preserves the remaining tool-call fields

#### Scenario: WebSocket response.create omits tool-call namespaces upstream

- **WHEN** a Responses WebSocket request replays namespaced `function_call` and `custom_tool_call` input items
- **THEN** the upstream `response.create` frame omits only those items' `namespace`
- **AND** preserves their remaining call fields

#### Scenario: Configured Responses model source omits tool-call namespaces upstream

- **WHEN** `/v1/responses` routes a replayed namespaced tool call to a configured OpenAI-compatible Responses model source
- **THEN** the source payload omits only the call item's `namespace`
- **AND** preserves source-compatible request fields that the Codex upstream path does not support

#### Scenario: Account-neutral replay classification retains namespace identity

- **WHEN** an HTTP bridge evaluates a namespaced tool-call history for cross-account replay safety
- **THEN** the classifier input retains the namespace metadata
- **AND** the request fails closed rather than becoming account-neutral because of wire normalization

#### Scenario: Malformed replay item type does not fail serialization

- **WHEN** a permissively parsed input item has a non-string `type` and a `namespace`
- **THEN** outbound serialization does not raise an internal type error
- **AND** does not treat the item as a recognized replayed tool call

#### Scenario: Top-level namespace tool remains byte-preserved

- **WHEN** the client includes a top-level tool entry whose `type` is `namespace`
- **THEN** standard Responses serialization forwards that tool entry byte-identically
