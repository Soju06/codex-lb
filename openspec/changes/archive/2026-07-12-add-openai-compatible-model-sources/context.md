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
- Requests to a source are always fixed-shape POSTs:
  `<base_url>/chat/completions` or `<base_url>/responses` with a JSON body, or
  `<base_url>/audio/transcriptions` with multipart form data. An attacker
  cannot choose the method, path suffix, or arbitrary headers.
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

Reasoning is a per-model capability, not something the proxy disables: a
model that genuinely has a thinking mode is marked with the "Reasoning"
capability toggle in the source dialogs (stored as
`"supports_reasoning": true` in the model's `raw_metadata_json`). For opted-in
models the proxy forwards client reasoning fields untouched and advertises
`supports_reasoning: true` in `/v1/models`; for everything else the metadata
stays `false` and no reasoning fields reach the upstream, so no
`message.reasoning` should appear.

The proxy never remaps `message.reasoning`/`message.content` — source
responses pass through byte-for-byte in both stream and non-stream modes. If
the upstream returns `content: null` with everything in `reasoning` (e.g. the
whole budget spent in the thinking phase, `finish_reason: "length"`), the
reasoning→final-answer boundary is being missed **server-side**: check that
the inference server uses the correct reasoning parser / chat template for the
model (e.g. vLLM `--reasoning-parser` matching the Qwen3 `</think>` format)
and that the model actually emits a final segment after thinking.

## Pricing semantics

Per-model pricing (`input_per_1m`, `cached_input_per_1m`, `output_per_1m`,
USD per 1M tokens) feeds API-key cost settlement and request-log `cost_usd`.
Cached input tokens are billed at the cached rate and subtracted from billable
input, mirroring subscription pricing. A model entry with no pricing fields
settles at $0 (unknown cost), same as unknown subscription models.

Audio transcription sources bill by duration when the source model declares an
`audio_per_minute` rate: the proxy reads the transcription's audio length
(top-level `duration` seconds, or a `usage.seconds`/`usage.duration` fallback)
and settles `duration_minutes * audio_per_minute` as cost with zero tokens.
Duration billing takes precedence over token pricing on the transcription
route, so a `total_tokens`-limited key is unaffected by ASR (0 tokens) while a
`cost_usd`-limited key is charged the per-minute cost. When the model has no
`audio_per_minute` rate, settlement falls back to token-compatible JSON `usage`
(`prompt_tokens`/`completion_tokens`, `input_tokens`/`output_tokens`, or
`total_tokens`); limited API keys still fail closed with `usage_unavailable`
only when neither a duration cost nor token usage is available.

Two operational notes on duration billing:

- The response must actually carry a duration. Strictly OpenAI-shaped servers
  only include `duration` with `response_format=verbose_json` (plain `json`
  and `text` omit it), so a duration-priced model called with those formats by
  a limited key fails closed with `usage_unavailable`. Many Whisper-compatible
  servers include `duration` in every JSON response, which works as-is.
- Cost limits reserve before the upstream responds, and the real audio cost is
  only known at settlement, so a single long file can finish past the
  `cost_usd` cap. The overshoot is bounded to that one request: once
  `current_value >= max_value` the next request is rejected before
  forwarding. This matches the general usage-reservation semantics; a strict
  prepaid cap would need pre-forward duration probing as a separate change.
