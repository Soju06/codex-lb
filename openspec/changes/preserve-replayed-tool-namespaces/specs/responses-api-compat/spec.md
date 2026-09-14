# responses-api-compat delta

## MODIFIED Requirements

### Requirement: Replayed tool-call namespace metadata follows the target wire boundary

For ChatGPT standard and compact Responses requests, including HTTP bridge
forwarding and upstream Responses WebSocket `response.create` frames, the proxy
MUST preserve `namespace` on every replayed `input` item whose `type` is
`function_call`, `custom_tool_call`, or `apply_patch_call` before forwarding
the request upstream. The proxy MUST preserve the item's `name` and every
other field, MUST retain the namespace for local call-identity,
replay-deduplication, and account-neutral replay-safety processing, and MUST
NOT alter client-provided top-level tool entries as part of this normalization.

When the request is routed to a configured OpenAI-compatible Responses model
source, the source payload MUST remove `namespace` from those recognized input
items immediately before source dispatch, while preserving their remaining
fields and source-compatible request fields. This source-only normalization
MUST NOT be reused for the ChatGPT wire paths.

#### Scenario: Standard Responses replay retains tool-call namespaces upstream

- **WHEN** a standard Responses request replays `function_call` and
  `custom_tool_call` input items with `namespace`
- **THEN** the ChatGPT upstream payload retains each item's `namespace` and
  `name`
- **AND** preserves all remaining call fields
- **AND** the local request input and replay-safety projection retain the
  namespace metadata

#### Scenario: Compact Responses replay retains tool-call namespace upstream

- **WHEN** `/v1/responses/compact` or a Responses-Lite request replays a
  recognized tool-call input item with a namespace
- **THEN** its ChatGPT upstream payload retains the input item's `namespace`
- **AND** preserves the remaining tool-call fields

#### Scenario: WebSocket response.create retains tool-call namespaces upstream

- **WHEN** a Responses WebSocket request replays namespaced `function_call` and
  `custom_tool_call` input items
- **THEN** the upstream `response.create` frame includes each item's original
  `namespace` and other fields
- **AND** preserves their remaining call fields

#### Scenario: Configured Responses model source omits tool-call namespaces upstream

- **WHEN** `/v1/responses` routes a replayed namespaced tool call to a
  configured OpenAI-compatible Responses model source
- **THEN** the source payload omits only the call item's `namespace`
- **AND** preserves source-compatible request fields that the ChatGPT upstream
  path does not support

#### Scenario: Account-neutral replay classification retains namespace identity

- **WHEN** an HTTP bridge evaluates namespaced tool-call history for
  cross-account replay safety
- **THEN** the classifier input retains the namespace metadata
- **AND** the request does not become account-neutral merely because a generic
  source would strip the field

#### Scenario: Malformed replay item type does not fail serialization

- **WHEN** a permissively parsed input item has a non-string `type` and a
  `namespace`
- **THEN** outbound serialization does not raise an internal type error
- **AND** does not treat the item as a recognized replayed tool call

#### Scenario: Top-level namespace tool remains byte-preserved

- **WHEN** the client includes a top-level tool entry whose `type` is
  `namespace`
- **THEN** standard Responses serialization forwards that tool entry
  byte-identically

## RENAMED Requirements

### Requirement: Replayed tool-call namespace metadata is local-only on upstream input

- **FROM:** Replayed tool-call namespace metadata is local-only on upstream input
- **TO:** Replayed tool-call namespace metadata follows the target wire boundary
