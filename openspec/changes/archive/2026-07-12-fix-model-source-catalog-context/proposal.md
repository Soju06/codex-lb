# fix-model-source-catalog-context

## Why

Codex CLI/IDE clients cannot use OpenAI-compatible source models that omit catalog metadata: an absent `contextWindow` surfaces as a zero-token budget, and the Codex client rejects entries that lack `shell_type` and the other client-capability fields it expects from `/backend-api/codex/models`. Separately, source-routed Responses forwarding sends Codex-only tool payloads (`namespace`, hosted `web_search`) that plain OpenAI-compatible providers reject, and local providers such as Ollama need operator-set request options (for example `num_ctx`) that clients never send.

## What Changes

- Codex catalog entries for OpenAI-compatible source models default the context window to 128,000 tokens when the source model has no configured `contextWindow`, and fill in the Codex client-capability defaults (`shell_type`, `max_context_window`, `truncation_policy`, `include_skills_usage_instructions`, `supports_image_detail_original`, `supports_search_tool`, `use_responses_lite`, `experimental_supported_tools`).
- Source-routed Responses forwarding drops non-`function` tools the source model has not declared support for. Support is declared per model in `raw_metadata_json`: `"supports_search_tool": true` keeps web-search tools; `"experimental_supported_tools"` lists additional tool types. When only some tools are dropped, a `tool_choice` referencing a dropped tool is removed so the forwarded payload stays self-consistent; when all tools are dropped, `tools`, `tool_choice`, and `parallel_tool_calls` are removed together.
- Per-model `"source_request_overrides"` in `raw_metadata_json` is applied to forwarded Responses payloads (with `options` merged key-wise). The proxy-owned `model` and `stream` keys cannot be overridden, and the override config is operator-side only: it is stripped from all client-visible catalog payloads.

## Scope

- `app/modules/model_sources/catalog.py`: source-model catalog defaults, override/capability metadata readers.
- `app/modules/proxy/api.py`: source-routed Responses forwarding (override application and tool filtering).
- No database schema changes; `raw_metadata_json` already exists on source models.
- No dashboard changes.

## Impact

- Modified capability: `model-catalog-compat` (Codex catalog entries for source models; catalog metadata hygiene).
- Modified capability: `responses-api-compat` (source-routed Responses forwarding).
- Tests: `tests/unit/test_model_sources_catalog.py`, `tests/integration/test_v1_models.py`, `tests/integration/test_api_keys_api.py`.
