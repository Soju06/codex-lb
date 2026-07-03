# Context: add-openai-compatible-model-sources

## Security considerations

### Source base URLs and SSRF (accepted risk)

`_normalize_base_url` accepts any absolute `http(s)` URL, including loopback,
link-local, and private-network addresses. This is deliberate: the primary use
case for OpenAI-compatible model sources is self-hosted inference servers
(vLLM, Ollama, llama.cpp, LM Studio) running on `localhost` or inside the same
private network as codex-lb, so an allowlist or private-range block would break
the feature's main deployment shape.

The resulting SSRF surface is bounded by:

- Source creation/update is dashboard-admin gated. Only operators with write
  access to the settings UI (or the admin API) can point the proxy at a new
  URL, and those operators already control the host codex-lb runs on.
- Requests to a source are always `POST <base_url>/chat/completions` or
  `POST <base_url>/responses` with a JSON body; an attacker cannot choose the
  method, path suffix, or arbitrary headers.
- The stored source API key is only ever attached to requests to that source's
  own `base_url` (`Authorization: Bearer` from the Fernet-encrypted secret).

Operators exposing the dashboard to semi-trusted users should treat "can edit
model sources" as equivalent to "can make the server POST to internal
endpoints and read the response" and restrict dashboard access accordingly
(e.g. cloud metadata endpoints respond to GET and are not reachable through
the fixed POST shape, but internal HTTP services accepting POST are).

### Deleted assigned sources keep keys deny-all

A source-scoped API key whose assigned sources are all deleted stays scoped
(`source_assignment_scope_enabled=true`, empty assigned set) and matches no
source. The dashboard edit dialog requires an explicit "Remove source
restriction" opt-in before it submits an empty assignment list, because the
backend interprets an empty list as disabling scoping.

## Known non-enforced fields

- `max_concurrency` is stored, migrated, and rendered in the settings UI, but
  no runtime code currently enforces a per-source concurrency ceiling.
- `health_status` is stored and rendered but nothing updates it after
  creation; sources always report `unknown` until a health-refresh mechanism
  exists.

Both are forward-looking configuration surface; treat them as inert until a
follow-up change wires enforcement.

## Outbound chat payload sanitization

`ChatCompletionsRequest` allows extra fields and defaults `tools` to `[]`, so
an unfiltered dump forwarded `"tools": []` and client-side reasoning toggles
(`include_reasoning`, `separate_reasoning`, `stream_reasoning`, `reasoning`,
`reasoning_effort` — OpenRouter/SGLang style) verbatim to sources. Against a
vLLM Qwen deployment this flipped the upstream into reasoning-extraction mode:
the whole answer landed in `message.reasoning` with `content: null` and
`finish_reason: "length"`. Source-routed chat now omits empty tool arrays
(plus `tool_choice`/`parallel_tool_calls`) and strips the reasoning toggles
unless the model's `raw_metadata_json` opts in with
`"supports_reasoning": true`. An API key's enforced reasoning effort is still
applied after sanitization (explicit operator policy wins).

Note: if the inference server itself enables thinking by default (e.g. vLLM
`--reasoning-parser` with a Qwen3 chat template), output can still be routed
to `message.reasoning` server-side; that must be disabled in the server's own
config (`chat_template_kwargs: {"enable_thinking": false}` or equivalent).

## Pricing semantics

Per-model pricing (`input_per_1m`, `cached_input_per_1m`, `output_per_1m`,
USD per 1M tokens) feeds API-key cost settlement and request-log `cost_usd`.
Cached input tokens are billed at the cached rate and subtracted from billable
input, mirroring subscription pricing. A model entry with no pricing fields
settles at $0 (unknown cost), same as unknown subscription models.
