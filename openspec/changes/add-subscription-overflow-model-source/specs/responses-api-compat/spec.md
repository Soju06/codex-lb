## ADDED Requirements

### Requirement: Source-routed Responses bodies are stripped of Codex client telemetry

When the proxy forwards a Responses request body to an OpenAI-compatible model source -- direct source routing and every subscription-overflow dispatch alike -- it SHALL remove exactly the top-level fields `client_metadata`, `stream_options` and `access_programs` from the forwarded body and MUST forward every other field the client sent unchanged, including `prompt_cache_key` (verbatim, so the source's prompt cache survives across turns), `tools` (byte-preserved), `include`, `max_output_tokens`, `metadata`, `truncation`, `prompt_cache_retention`, `background`, `max_tool_calls`, `top_logprobs` and any field the proxy does not recognise. The proxy MUST NOT reject, decline or reshape a source-routed request because of an unrecognised field: direct source routing never fails closed on an unseen field. `service_tier` MUST be removed only for subscription-overflow dispatch, where source pricing has no tier dimension and the source reservation settles without a tier; direct source routing MUST keep forwarding the client's `service_tier`. The stripping MUST be a pure, in-place projection of the forwarding dump (`model_dump_for_forwarding()`) with the field set defined once (`STRIPPED_TELEMETRY_FIELDS`), and MUST run before any other source-body shaping step so those steps and the overflow portability view operate on the same stripped body.

#### Scenario: Standard Codex turn loses exactly its telemetry

- **WHEN** a gpt-5.5-shaped Codex Responses body carrying `client_metadata`, `stream_options`, `prompt_cache_key`, `include: ["reasoning.encrypted_content"]` and `tools` is forwarded to a model source
- **THEN** the forwarded body omits `client_metadata` and `stream_options` and no other field
- **AND** `prompt_cache_key`, `include` and `tools` are forwarded verbatim

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
