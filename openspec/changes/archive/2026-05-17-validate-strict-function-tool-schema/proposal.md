# Why

A strict-mode JSON schema violation on a function tool (e.g. `tools[i].function.parameters` with `strict: true` but missing `additionalProperties: false`) currently leaks all the way to the upstream Codex backend, which closes the websocket with `close_code=1000`. codex-lb surfaces this as a generic `502 server_error / upstream_rejected_input` (streaming) or `502` HTTP body (non-streaming). Real OpenAI returns a deterministic `400 invalid_function_parameters` for the same payload (see https://github.com/github/github-mcp-server/issues/376 and OpenAI Structured Outputs docs).

Two consequences:

1. **Wrong HTTP class on deterministic client errors.** Clients with retry/failover loops (Hermes Agent, agentic frameworks) classify 5xx as transient and retry into a flood for a permanently-broken request. This is the same class of bug the skill identifies as "the 502-vs-400 quirk" — strict-schema violations on `text.format.json_schema` are already pre-validated by `enforce_strict_text_format`; function tools are the only un-validated strict surface left.
2. **Inconsistency between `/v1/chat/completions` and `/v1/responses`.** `_normalize_chat_tools` in `app/core/openai/chat_requests.py` silently drops the `strict` field while building the explicit-field tool dict, so chat-completions clients with invalid strict schemas appear to succeed (200 + `tool_calls`) while `/v1/responses` clients with the same logical payload get the 502. The chat path is silently *masking* a real schema problem the user asked us to enforce; the responses path is *misreporting* it.

# What Changes

- Add `validate_strict_function_tool_schema()` to `app/core/openai/strict_schema.py`, mirroring the existing `validate_strict_json_schema()` but specialized for `function` tool entries: applies the strict-mode rules (`additionalProperties: false`, every property in `required`, no empty `{}` nodes) to `tool.parameters` when `tool.strict is True`.
- Add `enforce_strict_function_tools_format()` to `app/modules/proxy/request_policy.py` and call it from `normalize_responses_request_payload()` next to `enforce_strict_text_format()`. Same deterministic 400 envelope shape: `code=invalid_function_parameters`, `param=tools[<i>].function.parameters` (chat-style) or `tools[<i>].parameters` (responses-style), `type=invalid_request_error`. Message format mirrors real OpenAI: `"Invalid schema for function '<name>': In context=<path>, <reason>."`.
- Stop dropping the `strict` field in `_normalize_chat_tools` (`app/core/openai/chat_requests.py`). It is now preserved into the responses-side tool dict so the new enforcement covers chat-completions traffic via the existing coercion → normalize → enforce pipeline.
- Update `scripts/verify_v1_responses_openai_sdk.py::case_tool_call_streaming` to use a spec-compliant strict schema (`additionalProperties: false`), so the SDK e2e suite passes 5/5 instead of 4/5.
- New unit + integration coverage exercising the four-cell matrix (strict ∈ {true, false}, additionalProperties ∈ {present, missing}) for both `/v1/chat/completions` and `/v1/responses`. Pin: A-cell → 400, others → 200/2xx.

# Out of Scope

- `text.format.json_schema` strict enforcement is unchanged — already covered by `enforce_strict_text_format`.
- `tool_choice` and tool-type registry behavior are unchanged. Only `function` tools' `parameters` schema is newly validated.
- Built-in tools (`web_search`, `image_generation`, etc.) do not accept user-supplied schemas; not in scope.
