# responses-api-compat delta

## MODIFIED Requirements

### Requirement: Responses input images bypass the HTTP bridge

The service MUST bypass the HTTP responses bridge for a `/v1/responses`,
`/backend-api/codex/responses`, `/responses/compact`, or
`/v1/responses/compact` request only when the request carries an image the
bridge cannot carry, and send that request over the raw (non-bridge) Responses
stream path. A request carries such an image when any of the following holds:

1. an `input_image` part anywhere in `input` — a top-level item, nested
   message content, or tool-output content at any depth, in the current turn
   or in replayed history — has an `image_url` that is an external `http://`
   or `https://` URL, with the scheme matched case-insensitively;
2. an `input_image` part of any form appears in the current turn;
3. `tools` contains a built-in `image_generation` tool.

The current turn is the sequence of `input` items after the last model-output
item. A model-output item is a message with `role = "assistant"` or an item
whose `type` is `reasoning`, `function_call`, `custom_tool_call`, or
`apply_patch_call`. When `input` contains no model-output item, all of `input`
is the current turn.

An `input_image` part whose `image_url` is an inline `data:` URL and which sits
before the last model-output item — including one nested in a replayed
`function_call_output`, `custom_tool_call_output`, or `apply_patch_call_output`
— MUST NOT by itself cause the bypass. Such a request MUST remain eligible for
the HTTP responses bridge under the ordinary bridge admission and the other
bypass gates (oversized payload, explicit HTTP upstream transport, recent
upstream WebSocket failure), and the bridge MUST forward those historical
inline images without stripping or rewriting them.

This bypass MUST happen after rejecting unsupported uploaded-image references
and MUST be limited to the current request; subsequent requests that no longer
meet the conditions above MAY continue using the HTTP responses bridge. When
the bypass fires the service MUST record
`http_bridge_routing{stage="bypass",reason="image"}`; it MUST NOT record that
reason for a request that stays bridged.

The raw (non-bridge) path is the source of truth for validation and upstream
error semantics of current-turn images. The bridge MUST NOT hold a request
whose current turn carries an image waiting for `response.created` when
upstream rejects an invalid inline image payload.

This bridge bypass MUST NOT by itself pin the upstream stream transport. The
upstream transport for a bypassed image request MUST be resolved by the ordinary
upstream-transport precedence.

#### Scenario: Replayed inline image in a historical tool output stays on the bridge

- **GIVEN** the HTTP responses bridge is enabled, `upstream_stream_transport`
  is `"auto"`, and the effective downstream-HTTP policy admits the request to
  the bridge
- **AND** the serialized payload is below the WebSocket frame budget
- **WHEN** a `/v1/responses` request replays full history without
  `previous_response_id`, with `input` = a user message, a `function_call`
  with `call_id = "c1"`, a `function_call_output` with `call_id = "c1"` whose
  `output` array holds `{"type":"input_image","image_url":"data:image/png;base64,..."}`,
  an assistant message, and a final user text message
- **THEN** the request is served through the HTTP responses bridge and not
  through the raw (non-bridge) stream path
- **AND** `http_bridge_routing{stage="bypass",reason="image"}` is not recorded
  for that request
- **AND** the `response.create` sent upstream carries the historical
  `data:` image URL byte-identical to the request

#### Scenario: Nested input_image bypasses bridge

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** a Responses request's current turn contains a nested content part
  with `type = "input_image"` — a user message with an inline `data:` image, or
  a `function_call_output` after the last `function_call` whose output holds an
  inline `data:` image
- **THEN** the request is sent through the raw (non-bridge) stream path
- **AND** the HTTP responses bridge is not used for that request
- **AND** `http_bridge_routing{stage="bypass",reason="image"}` is recorded

#### Scenario: External image URL still bypasses the bridge wherever it sits

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** a Responses request carries an `input_image` part whose `image_url`
  is `https://example.com/cat.png`, either in the current turn or nested in a
  `function_call_output` output array before the last assistant message
- **THEN** the request is sent through the raw (non-bridge) stream path
- **AND** `http_bridge_routing{stage="bypass",reason="image"}` is recorded
- **AND** the upstream transport follows the external-image-URL clause of the
  downstream-HTTP upstream transport precedence

#### Scenario: Image-generation request still bypasses the bridge

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** a Responses request with text-only `input` includes a top-level
  `{"type":"image_generation"}` tool
- **THEN** the request is sent through the raw (non-bridge) stream path
- **AND** `http_bridge_routing{stage="bypass",reason="image"}` is recorded

#### Scenario: Image bypass does not disable future text bridge use

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** an image-bearing request bypasses the bridge
- **THEN** the bypass applies only to that request
- **AND** a later request that meets none of the bypass conditions can still use
  the HTTP responses bridge

#### Scenario: Image bypass does not pin the upstream transport

- **GIVEN** the HTTP responses bridge is enabled
- **AND** `upstream_stream_transport` is `"auto"`
- **WHEN** a Responses request carrying an inline `data:` image below the
  WebSocket frame budget bypasses the bridge
- **THEN** the request MUST NOT be forced onto upstream HTTP
- **AND** the configured transport policy MUST decide its upstream transport
