## ADDED Requirements

### Requirement: Source-routed Responses bodies are stripped of Codex client telemetry

When the proxy forwards a Responses request body to an OpenAI-compatible model source -- direct source routing and every subscription-overflow dispatch alike -- it SHALL remove exactly the Codex client telemetry from the forwarded body: the top-level fields `client_metadata` and `access_programs` whole (neither is a Responses API field), and from the `stream_options` object exactly the Codex key `reasoning_summary_delivery`, dropping the `stream_options` object only when that removal leaves it empty (the shape of every Codex body). `stream_options` is a standard Responses field, so a `stream_options.include_obfuscation` an SDK client sends MUST be forwarded unchanged, and a `stream_options` value that is not an object MUST be forwarded untouched for the source to judge. The proxy MUST forward every other field the client sent unchanged, including `prompt_cache_key` (verbatim, so the source's prompt cache survives across turns), `tools` (byte-preserved), `include`, `max_output_tokens`, `metadata`, `truncation`, `prompt_cache_retention`, `background`, `max_tool_calls`, `top_logprobs` and any field the proxy does not recognise. The proxy MUST NOT reject, decline or reshape a source-routed request because of an unrecognised field: direct source routing never fails closed on an unseen field. `service_tier` MUST be removed only for subscription-overflow dispatch, where source pricing has no tier dimension and the source reservation settles without a tier; direct source routing MUST keep forwarding the client's `service_tier`. The stripping MUST be a pure, in-place projection of the forwarding dump (`model_dump_for_forwarding()`) with the field and key sets defined once (`STRIPPED_TELEMETRY_FIELDS`, `STRIPPED_STREAM_OPTIONS_KEYS`), and MUST run before any other source-body shaping step so those steps and the overflow portability view operate on the same stripped body. A `stream_options` object that survives the projection is outside the overflow view's allowlist and declines the overflow view as an unknown field like any other unlisted field, so overflow stays Codex-shaped while direct routing forwards it.

#### Scenario: Standard Codex turn loses exactly its telemetry

- **WHEN** a gpt-5.5-shaped Codex Responses body carrying `client_metadata`, `stream_options: {"reasoning_summary_delivery": "interleaved"}`, `prompt_cache_key`, `include: ["reasoning.encrypted_content"]` and `tools` is forwarded to a model source
- **THEN** the forwarded body omits `client_metadata` and `stream_options` (emptied by the removal of its only key) and no other field
- **AND** `prompt_cache_key`, `include` and `tools` are forwarded verbatim

#### Scenario: Standard stream_options survive the projection

- **WHEN** a Responses body carrying `stream_options: {"include_obfuscation": false, "reasoning_summary_delivery": "interleaved"}` is forwarded to a model source
- **THEN** the forwarded body carries `stream_options: {"include_obfuscation": false}`
- **WHEN** a Responses body carries `stream_options: {"include_obfuscation": true}` alone
- **THEN** `stream_options` is forwarded unchanged
- **AND** the overflow portability view of that stripped body declines with `not_portable_unknown_field` naming `stream_options`

#### Scenario: Unknown fields are forwarded, never fail closed

- **WHEN** a source-routed Responses body carries a top-level field the proxy has never seen
- **THEN** the field is forwarded to the source unchanged
- **AND** the request is neither rejected nor declined because of it

#### Scenario: service_tier is stripped only for overflow dispatch

- **WHEN** a Responses body with `service_tier: "priority"` is forwarded by direct source routing
- **THEN** `service_tier` is forwarded unchanged
- **WHEN** the same body is dispatched to the designated subscription-overflow source
- **THEN** `service_tier` is removed from the forwarded body

### Requirement: Provider-portable Responses bodies are classified with closed decline reasons

The proxy SHALL decide whether a stripped Responses body can be served by a standard OpenAI-compatible model source in two pure steps that touch no account or source state and never raise. First, the overflow portability view MUST admit exactly the top-level fields `model`, `input`, `instructions`, `tools`, `tool_choice`, `parallel_tool_calls`, `reasoning`, `text`, `include`, `store`, `stream`, `truncation`, `max_output_tokens`, `temperature`, `top_p`, `metadata`, `user`, `safety_identifier`, `prompt_cache_key`, `prompt_cache_retention`, `previous_response_id`, `conversation` and `prompt` (`OVERFLOW_VIEW_FIELDS`, defined once); any other top-level field MUST decline the body with `not_portable_unknown_field` naming the field, a `reasoning` object with any key other than `effort` or `summary` (the Responses-Lite `context`) or an `additional_tools` input item (the Responses-Lite tool bundle) MUST decline it with `not_portable_lite_namespace`, and the view MUST be a copy of the stripped body so later shaping cannot alter the evidence. Second, the verdict MUST be evaluated on that view -- never on the raw body -- and MUST return `portable` or exactly one reason from the closed set `not_portable_history`, `not_portable_lite_namespace`, `not_portable_tools`, `not_portable_items`, `not_portable_vision`, `not_portable_unknown_field`, `turn_state_bound`, evaluated in this order: an `additional_tools` item -> `not_portable_lite_namespace`; a `tools[]` entry whose `type` is not `function` and is not a type the source model declares from the portable set -- `custom`, `web_search`/`web_search_preview` (validated by the account-neutral predicate) or the stateless Codex tool types `apply_patch`, `shell`, `local_shell`, `tool_search` in exactly their stateless shape (`type` plus an optional string `description`; any other field on such a declaration is `not_portable_tools` naming the type) -- is `not_portable_tools`; `namespace` (the reserved code-mode/collaboration tool) and hosted tool types such as `code_interpreter`, `file_search`, `mcp`, `image_generation` and `computer_use_preview` are `not_portable_tools` even when declared, because their declarations carry provider- or account-side state (containers, vector stores, connectors) that no source can serve portably; an input item whose type is neither provider-universal (`message`, `function_call`, `function_call_output`) nor a tool-call item whose tool type the source model declares (`custom_tool_call`/`custom_tool_call_output` -> `custom`, `apply_patch_call`/`apply_patch_call_output` -> `apply_patch`, `web_search_call` -> `web_search`, `tool_search_call`/`tool_search_output` -> `tool_search`, `local_shell_call`/`local_shell_call_output` -> `local_shell`, `shell_call`/`shell_call_output` -> `shell`) -> `not_portable_items`, with response-owned history items left to the history check; an `input_image` part in message content or tool output without the source model's `supports_vision` -> `not_portable_vision`; a body that is not an account-neutral fresh replay of the view restricted to the fields the account-neutral predicate validates -- where a `tools[]` declaration (or a bare `tool_choice`) of one of the stateless Codex tool types the source model declares is set aside from that check only in exactly its stateless shape -- `type` plus an optional string `description` -- so no reference-bearing field (`container`, `container_id`, `file_ids`, `vector_store_ids`, hosted URLs) can ride along, and hosted declarations are never set aside -- or that carries a `reasoning` or `compaction` item -> `not_portable_history`; a non-blank `x-codex-turn-state` header that is not a proxy-synthesized value -> `turn_state_bound`. Configuration-class reasons MUST take precedence over `not_portable_history` because only `not_portable_history` may ever earn the client a "start a new conversation" hint, and a body a new conversation reproduces identically must never receive it. A `portable` verdict MUST imply that the classified view is an account-neutral fresh replay. `transcript_is_source_free` MUST answer exactly the history check (account-neutral fresh replay with no `reasoning`/`compaction` item). Responses-Lite (gpt-5.6) bodies are out of scope for overflow in this version and MUST always decline with `not_portable_lite_namespace`, never with `not_portable_history`. Tool-type declarations come from the source model's `supports_search_tool`/`experimental_supported_tools` metadata and vision from its `supports_vision` flag; declaring the type or vision MUST restore portability for bodies declined only for that reason. Decline details (field names, item and tool types) are operator diagnostics and MUST NOT appear in client responses. Neither step MAY raise on a body the request model admits: a malformed nested value in a known slot (for example `tool_choice: {"type": []}` or a `web_search` tool with `search_context_size: []`) MUST decline as `not_portable_history`, and the account-neutral replay predicate itself MUST answer false rather than fail for such values; `transcript_is_source_free`, exposed on its own for the neutral-release check, MUST decline malformed input items the same way.

#### Scenario: Standard first turn is portable once the source declares the Codex tool types

- **WHEN** a stripped gpt-5.5-shaped Codex first-turn body (function tools, the `custom` `apply_patch` tool, `reasoning.effort`/`summary`, `include: ["reasoning.encrypted_content"]`, `prompt_cache_key`) is classified for a source model that has not declared `custom`
- **THEN** the verdict is `not_portable_tools` naming `custom`
- **WHEN** the source model declares `custom`
- **THEN** the verdict is `portable`
- **AND** the classified view is an account-neutral fresh replay

#### Scenario: Responses-Lite bundle declines as Lite, never as history

- **WHEN** a gpt-5.6 Responses-Lite body (an `additional_tools` item of `namespace` tools, the tagged developer base-instructions message, `reasoning.context: "all_turns"`, no top-level `tools`) is stripped and viewed
- **THEN** the view declines with `not_portable_lite_namespace`
- **AND** the stripped body still forwards unchanged for direct source routing
- **AND** no evaluation path reports the bundle as `not_portable_history`

#### Scenario: Reserved namespace tool declines as tools ahead of history

- **WHEN** a body carries a top-level `tools[]` entry of type `namespace` together with a `previous_response_id`
- **THEN** the verdict is `not_portable_tools` naming `namespace`
- **AND** it stays `not_portable_tools` even if the source model lists `namespace` among its declared tool types

#### Scenario: Unknown top-level field declines the view only

- **WHEN** a stripped body carries a top-level field outside the view allowlist
- **THEN** the view declines with `not_portable_unknown_field` naming the field
- **AND** the same body is still forwarded unchanged by direct source routing

#### Scenario: Mid-thread history declines as history

- **WHEN** a body carries retained `reasoning` items with `encrypted_content`, items with response-owned ids, `previous_response_id`, `conversation`, `prompt`, an `item_reference`, or a hosted tool item, and every tool and item type is declared
- **THEN** the verdict is `not_portable_history`
- **AND** `transcript_is_source_free` is false for that view

#### Scenario: Declared stateless tool types are portable, malformed nested values never raise

- **WHEN** a fresh body declares `tools: [{"type": "apply_patch"}]` and the source model has not declared `apply_patch`
- **THEN** the verdict is `not_portable_tools` naming `apply_patch`
- **WHEN** the source model declares `apply_patch`
- **THEN** the verdict is `portable`
- **WHEN** a declared `apply_patch` declaration carries any field beyond `type`/`description`, such as `container: "cntr_previous"` or `file_ids`
- **THEN** the verdict is `not_portable_tools` naming `apply_patch` and the declaration is never set aside
- **WHEN** the source model declares `code_interpreter` and the body declares `tools: [{"type": "code_interpreter", "container": "cntr_previous"}]`
- **THEN** the verdict is `not_portable_tools` naming `code_interpreter`
- **WHEN** a body admitted by the request model carries `tool_choice: {"type": []}`
- **THEN** the verdict is `not_portable_history` and no exception escapes the gate

#### Scenario: Images require vision and a binding turn state is the last reason

- **WHEN** an otherwise portable body carries an `input_image` part and the source model has `supports_vision` false
- **THEN** the verdict is `not_portable_vision`
- **WHEN** the source model has `supports_vision` true and the request carries a non-blank `x-codex-turn-state` that the proxy did not synthesize
- **THEN** the verdict is `turn_state_bound`
- **WHEN** the turn state is absent or proxy-synthesized
- **THEN** the verdict is `portable`

### Requirement: Overflow dispatch keeps one Responses lifecycle

A subscription-overflow dispatch SHALL present the source's Responses lifecycle to the client exactly as a directly source-routed request does: the wire is the source stream through the public source wrapper -- verbatim events for native Codex clients under the native failure-lifecycle preservation, the SDK-normalized stream with comment keepalives for other clients -- with one `response.created`, one terminal event and the source's ids, `model`, `created_at`, `usage` and `service_tier`. The proxy MUST NOT start the subscription generator, splice streams, rewrite ids, emit `codex.rate_limits` or turn-state events, or set `x-codex-turn-state` or `X-Codex-LB-Prompt-Cache-Mode` on an overflow answer; rate-limit headers are the pool-aggregate values, so `/status` keeps showing the exhausted subscription pool. The only frames the proxy MAY synthesize are one `response.created` + `response.failed` pair (`error.code` `subscription_overflow_pin_unavailable`, `error.type` `server_error`) when the pin could not be made durable before any source frame was yielded; a non-streaming request in the same situation is answered HTTP `503` with that code. Overflow-specific answers MUST use these codes and no others: HTTP `503` `model_source_unavailable` (`error.type` `upstream_error`, `Retry-After: 2`) for a transient obstacle on a pinned or anchored conversation; HTTP `503` `model_source_busy` (`upstream_error`, `Retry-After: 1`) for a saturated source on a pinned or anchored conversation; HTTP `400` `subscription_overflow_source_unavailable` and `subscription_overflow_unsupported_input` (`invalid_request_error`) for permanently unservable pinned conversations; HTTP `426` `subscription_overflow_requires_http_transport` at a WebSocket handshake; the existing `503` `model_source_requires_http_transport` for an in-band WebSocket bounce. Source failures keep the source-route contract: the source's status and sanitized envelope with the source's `Retry-After`, `504` `model_source_timeout`, `502` `model_source_unreachable`, `502` `model_source_credentials_error` for a source `401`/`403`, `502` `usage_unavailable` for a non-streaming limited key without usage. The proxy MUST NEVER recode a source failure to `usage_limit_reached`, emit `previous_response_not_found` or a pre-visible `stream_incomplete` for an overflow dispatch, synthesize a `Retry-After` the source did not send, or use the codes `server_is_overloaded` or `slow_down` on any overflow answer, because Codex renders those as a non-retryable capacity error. `Retry-After` on proxy-owned `503` answers is an SDK courtesy; Codex never reads it. A source `429` passes through with the source's `Retry-After` and without `resets_at`; native Codex renders it as "exceeded retry limit, last status: 429" -- documented, not recoded.

#### Scenario: Native Codex receives the source lifecycle verbatim

- **WHEN** a native Codex request overflows to the designated source over `/backend-api/codex/responses`
- **THEN** the client receives the source's events verbatim with one `response.created` and one terminal event carrying the source's ids and usage
- **AND** no `codex.rate_limits` or turn-state event, no `x-codex-turn-state` header and no `X-Codex-LB-Prompt-Cache-Mode` header is sent
- **AND** the `x-codex-*` rate-limit headers describe the subscription pool

#### Scenario: SDK clients receive the normalized lifecycle

- **WHEN** an OpenAI SDK request overflows over `/v1/responses` with `stream: true`
- **THEN** the stream is normalized under the SDK contract with comment keepalives flowing while pre-content frames are withheld for the pin write
- **AND** exactly one `response.created` and one terminal event reach the client

#### Scenario: Pin failure yields one synthesized pair

- **WHEN** the pin cannot be made durable before the first content-bearing source frame
- **THEN** the client receives `response.created` followed by `response.failed` with `error.code` `subscription_overflow_pin_unavailable` and `error.type` `server_error`, and nothing else from the source
- **AND** a non-streaming request in the same situation is answered HTTP `503` `subscription_overflow_pin_unavailable`

#### Scenario: Source rate limits pass through honestly

- **WHEN** the designated source answers HTTP `429` with `Retry-After: 7` before any body
- **THEN** the client receives HTTP `429` with the source's sanitized envelope and `Retry-After: 7`, without `resets_at` and without `usage_limit_reached`

#### Scenario: Forbidden codes are never emitted

- **WHEN** any overflow answer is produced -- transient or permanent pinned answers, handshake denials, in-band bounces, pin failures
- **THEN** its `error.code` is one of `model_source_unavailable`, `model_source_busy`, `subscription_overflow_source_unavailable`, `subscription_overflow_unsupported_input`, `subscription_overflow_requires_http_transport`, `model_source_requires_http_transport` or `subscription_overflow_pin_unavailable`
- **AND** it is never `server_is_overloaded`, `slow_down`, `previous_response_not_found` or a recoded `usage_limit_reached`

### Requirement: Declined overflow keeps the usage-limit answer byte-identical except the history hint

Every request the overflow decision declines, and every decision failure outside a pinned or anchored context, SHALL be answered by the unchanged subscription path with the same `429` `usage_limit_reached` envelope a deployment without a designation returns for the same pool: same status, same headers other than date and request id, same body bytes, no `Retry-After`, rebuilt from the same selection answer. The only permitted difference is the history hint, set exclusively by the `not_portable_history` decline: for a native Codex request (native `User-Agent` prefix or a native `originator`) the proxy MUST add the header `x-codex-promo-message: Start a new conversation to continue on the configured overflow model source`, which Codex renders as "You've hit your usage limit. Start a new conversation to continue on the configured overflow model source, or try again at <resets_at>."; for any other client the proxy MUST append the sentence "This conversation cannot be moved to the configured overflow model source because it contains prior reasoning; a new conversation can be served by it." to `error.message`. Configuration-class declines (undeclared tool types, unsupported items, vision, unknown fields, Responses-Lite bodies, source or model unavailable) MUST carry no hint, because a new conversation would decline identically. The hint state is request-scoped and MUST be read only by the `429` `usage_limit_reached` renderer through one attribute read; no other status or code ever carries it, and the header is never added for an SDK client nor the sentence for a native one.

#### Scenario: Declines are byte-identical to today's answer

- **GIVEN** a stored designation and an exhausted pool
- **WHEN** a request is declined for `background_job`, `turn_state_bound`, `opportunistic`, `key_scope`, `no_thread_key`, `source_excluded`, `breaker_open`, `source_busy` or a configuration-class portability reason
- **THEN** the status, headers (other than date and request id) and body bytes equal the `429` `usage_limit_reached` answer of a deployment without a designation
- **AND** the source receives no request

#### Scenario: Native history hint

- **WHEN** a native Codex request whose input carries a `reasoning` item is declined `not_portable_history`
- **THEN** the `429` carries `x-codex-promo-message: Start a new conversation to continue on the configured overflow model source`
- **AND** the body bytes are otherwise identical to today's answer

#### Scenario: SDK history hint

- **WHEN** an OpenAI SDK request carrying `previous_response_id` owned by a subscription account, or a `reasoning` item, is declined `not_portable_history`
- **THEN** `error.message` ends with "This conversation cannot be moved to the configured overflow model source because it contains prior reasoning; a new conversation can be served by it."
- **AND** no `x-codex-promo-message` header is present

#### Scenario: No hint for configuration-class declines or without a designation

- **WHEN** a gpt-5.6 Responses-Lite body, a body with an undeclared tool type, or any request with both settings columns `null` is answered `429` `usage_limit_reached`
- **THEN** neither the header nor the sentence is present

### Requirement: WebSocket sessions deny with 426 only on pin, tombstone or bounce evidence and bounce fresh exhaustion in-band

On the `/backend-api/codex/responses` and `/v1/responses` WebSocket handshakes, when a designation is stored or a drain deadline is armed and the handshake carries `thread-id`, the proxy SHALL perform one bounded thread-pin lookup and no exhaustion probe: `live`, `expired` or `bounce` evidence MUST deny the handshake before it is accepted with HTTP `426` and `error.code` `subscription_overflow_requires_http_transport` (`error.type` `server_error`), writing no request-log row and counting `bounced_ws_handshake`; a lookup timeout MUST deny with `426` as well (fail-closed toward HTTP, counted `pinned_lookup_timeout`); without evidence the handshake MUST be accepted -- an exhausted pool alone never earns a speculative `426`. Handshakes carrying a required-capability header MUST never be denied by this rule, and with both settings columns `null` the check MUST return after two attribute reads. During an accepted session, when account selection for a `response.create` answers `usage_limit_reached` and the turn is eligible for fresh overflow (a thread key, no O(1) decline, a resolvable source model and a portable body), the proxy MUST write a bounce row (`bounce\n<thread key>`, 60 s, drain-capped; a write that is not `written` is logged and is not fatal, because the row is a latency optimisation) and emit the wrapped connect-failure event `{"type": "error", "status": 503, "error": {"code": "model_source_requires_http_transport", ...}}` with a top-level numeric `status`, releasing the turn's usage reservation and writing today's connect-failure row (counted `bounced_ws_event`); an ineligible turn MUST receive today's `usage_limit_reached` event unchanged, and a non-native turn without a thread key receives the `503` event only, with no bounce row. A `response.create` on a pinned or anchored conversation -- `live` or `expired` thread pin, or a live anchor for a `previous_response_id` without a recorded subscription owner -- MUST be bounced the same way on the first turn and on a reused socket alike, with the reservation released and the turn's row finalized; a lookup timeout bounces. The pinned check runs after the turn's usage reservation and before the turn is registered with the session's scope cleanup, so a scope cancellation delivered inside its settings read, a lookup or the bounce-row write MUST release the reservation and write the turn's `cancelled` row (`stream_incomplete`, as the scope cleanup does for a registered turn) before the cancellation propagates, and MUST NOT dispose a turn twice: a cancellation inside the connect-failure emitter, which owns its own release and row, adds nothing. The bounce is advisory: the HTTP route re-decides authoritatively after the client's downgrade. The event MUST never use `server_is_overloaded` or `slow_down` and MUST always carry the top-level `status`, because Codex treats the former as non-retryable and ignores an error event without a status. Codex `0.99.0` and newer switch the session to HTTP on the handshake `426` immediately and stay there until the process restarts (`codex resume` starts a new process whose first handshake meets the pin and is denied again); the in-band `503` enters Codex's retry ladder, which re-handshakes on every attempt, so the bounce row turns the next handshake into a `426`. The HTTP Responses session bridge never enters overflow.

#### Scenario: Live pin denies the handshake

- **GIVEN** a stored designation and a live thread pin for `thread-id` `t1`
- **WHEN** a Codex client opens a Responses WebSocket handshake carrying `thread-id: t1`
- **THEN** the handshake is denied before acceptance with HTTP `426` and `error.code` `subscription_overflow_requires_http_transport`
- **AND** no request-log row is written and `bounced_ws_handshake` is counted
- **AND** a tombstoned pin denies the same way

#### Scenario: Bounce evidence expires

- **GIVEN** a bounce row written 30 s ago for `t1`
- **WHEN** a handshake carrying `thread-id: t1` arrives
- **THEN** it is denied with HTTP `426`
- **WHEN** 60 s have passed since the bounce row was written
- **THEN** the handshake is accepted

#### Scenario: No speculative denial

- **GIVEN** a stored designation, an exhausted pool and no pin, tombstone or bounce row for the thread
- **WHEN** a handshake arrives
- **THEN** it is accepted and no exhaustion probe ran
- **WHEN** the handshake carries a required-capability header while a pin exists
- **THEN** it is accepted
- **WHEN** both settings columns are `null`
- **THEN** no pin lookup runs for any handshake

#### Scenario: Fresh exhaustion bounces in-band and the retry meets a 426

- **GIVEN** an accepted session whose `response.create` is portable, thread-keyed and eligible
- **WHEN** account selection answers `usage_limit_reached`
- **THEN** a bounce row for the thread is written and the client receives `{"type": "error", "status": 503, "error": {"code": "model_source_requires_http_transport"}}`
- **AND** the turn's reservation is released, today's connect-failure row is written and `bounced_ws_event` is counted
- **AND** the next handshake for the thread within 60 s is denied with HTTP `426`
- **WHEN** the turn is ineligible (a `reasoning` item, a background job, an opportunistic key, or no thread key on a native request)
- **THEN** the client receives today's `usage_limit_reached` event byte for byte
- **WHEN** a non-native session without a thread key is exhausted and otherwise eligible
- **THEN** the client receives the `503` event and no bounce row is written

#### Scenario: Pinned or anchored turns on an open socket are bounced

- **GIVEN** an accepted session with an open subscription upstream
- **WHEN** a `response.create` arrives for a pinned thread, or with a `previous_response_id` that resolves to a live anchor and no subscription owner
- **THEN** the client receives the `503` `model_source_requires_http_transport` event with top-level `status`, the reservation is released, the row is finalized and the frame is not forwarded
- **AND** a bounce row is written when the turn carries a thread key
- **WHEN** the pin lookup times out
- **THEN** the turn is bounced the same way

#### Scenario: A scope cancellation during the pinned check leaks nothing

- **GIVEN** a stored designation and a `response.create` carrying `thread-id` whose usage reservation exists but whose turn is not yet registered
- **WHEN** the session's task is cancelled while the thread-pin lookup (or the bounce-row write) is in flight
- **THEN** the reservation is released exactly once, one `cancelled` `stream_incomplete` row is written, no event is sent and the cancellation propagates
- **WHEN** the cancellation is delivered inside the connect-failure emitter instead
- **THEN** exactly one release and one row exist (the emitter's own)

#### Scenario: The bounce never uses a non-retryable code

- **WHEN** any in-band bounce event is emitted
- **THEN** its `error.code` is `model_source_requires_http_transport`, never `server_is_overloaded` or `slow_down`, and the event carries a numeric top-level `status`

### Requirement: Compaction on a pinned conversation is refused

Both compact routes, `/backend-api/codex/responses/compact` and `/v1/responses/compact`, SHALL check the thread pin after model-access validation and before any account selection or usage reservation when a designation is stored or a drain deadline is armed and the request carries `thread-id`: a `live` or `expired` pin MUST be refused with HTTP `400`, `error.code` `subscription_overflow_unsupported_input`, `error.type` `invalid_request_error` and the message "This conversation is being served by the overflow model source and cannot be compacted there yet; start a new conversation."; a lookup timeout MUST be answered HTTP `503` `model_source_unavailable`; without a pin the routes behave as before. A Responses request on a pinned conversation whose input ends with a `compaction_trigger` item MUST be refused with the same `400`. With both settings columns `null` no lookup runs. Codex has no local compaction fallback, so a pinned conversation whose context fills up must be restarted; forwarding compaction to the pinned source is deferred to a later version.

#### Scenario: Compact routes refuse a pinned conversation

- **GIVEN** a stored designation and a live or tombstoned thread pin for `t1`
- **WHEN** a request carrying `thread-id: t1` reaches `/backend-api/codex/responses/compact` or `/v1/responses/compact`
- **THEN** the response is HTTP `400` `subscription_overflow_unsupported_input` with the compaction message
- **AND** no account was selected and no reservation was taken
- **WHEN** the pin lookup times out
- **THEN** the response is HTTP `503` `model_source_unavailable`

#### Scenario: Unpinned compaction is unchanged

- **WHEN** a compact request arrives for a thread without a pin, or while both settings columns are `null`
- **THEN** the route behaves exactly as before this change
