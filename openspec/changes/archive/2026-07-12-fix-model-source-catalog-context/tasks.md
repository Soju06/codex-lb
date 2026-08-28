# Tasks

- [x] Default the source-model context window to 128k and fill in the Codex client-capability defaults (`shell_type`, `max_context_window`, `truncation_policy`, capability flags) in `source_models_to_upstream_models`
- [x] Strip `source_request_overrides` from the client-visible catalog metadata while keeping it readable server-side via `source_model_request_overrides`
- [x] Apply `source_request_overrides` to forwarded source Responses payloads with key-wise `options` merging, protecting the proxy-owned `model` and `stream` keys
- [x] Gate non-`function` tool filtering on per-model capability metadata (`supports_search_tool`, `experimental_supported_tools`) and remove a `tool_choice` that references a dropped tool
- [x] Add unit coverage for the catalog defaults, override readers, and supported-tool-type resolution
- [x] Add integration coverage at the failing surfaces: `/backend-api/codex/models` catalog shape and override-leak regression; `/backend-api/codex/responses` and `/v1/responses` forwarding with tool filtering, overrides, and the stream guard
- [x] Update the model-catalog-compat and responses-api-compat spec deltas and run strict OpenSpec validation
