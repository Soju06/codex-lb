## Outbound `service_tier` write audit

The application-wide audit separates request-payload writers from response,
pricing, persistence, and observability fields that happen to share the same
name.

### Outbound request writers and normalizers

- `app/core/openai/requests.py`: `ResponsesRequest` field validation and the
  compact/OpenAI-compatible mapping normalizers canonicalize inbound `fast` to
  `priority`. Covered: every resulting typed Responses payload reaches the
  shared prohibition after validation and before routing/serialization.
- `app/core/openai/v1_requests.py` and `app/core/openai/chat_requests.py`:
  typed `service_tier` fields are transferred into `ResponsesRequest` during
  `/v1` and chat conversion. Covered by the shared typed policy and route-level
  tests.
- `app/modules/proxy/request_policy.py::normalize_upstream_model_alias`:
  derives `priority` from qualified Fast Mode aliases. Covered: ordinary paths
  call the shared prohibition at the end of `apply_api_key_enforcement`, and
  warmup calls it immediately after alias normalization.
- `app/modules/proxy/request_policy.py::apply_api_key_enforcement`: writes an
  API key's enforced tier (or `None` for omit-equivalent `auto`/`default`).
  Covered: the shared prohibition runs after the write while preserving the
  function's pre-write provenance result.
- `app/modules/proxy/request_policy.py::apply_api_key_enforcement_to_chat_payload`:
  writes or removes an API-key enforced tier on source-routed chat dictionaries.
  Covered: `_source_chat_completion_response` applies the same shared helper
  after chat enforcement and before forwarding.
- `app/modules/proxy/api.py::_stream_responses`: restores the origin-signed
  effective tier on owner-forwarded payloads. Covered: the shared prohibition
  runs immediately after restoration, before account selection and upstream
  serialization.
- `app/modules/proxy/request_policy.py::apply_enforced_service_tier_model_fallback`:
  can only remove an enforced tier when the selected subscription model lacks
  it. Deliberately retained as an independent compatibility fallback; it never
  introduces priority, and the global prohibition runs after it at boundaries
  that can restore or rewrite a tier.
- `app/modules/proxy/request_policy.py::apply_prohibit_fast_mode`: the sole
  prohibition implementation. It removes canonical priority from typed
  Responses payloads and source-chat dictionaries and never writes a
  non-priority value.

### Same-name writes that are not outbound request writers

- Model/request field declarations in `app/core/openai/*.py` define typed
  schema surfaces; their actual transfers are covered above.
- `app/modules/proxy/_service/streaming/*`, `_service/websocket/*`,
  `_service/http_bridge/upstream_events.py`, and `_service/api_key_usage.py`
  assign observed upstream response tiers into request state, settlement, and
  logs after dispatch. They do not mutate a request that will be serialized
  upstream.
- `app/modules/automations/service.py` reads an upstream compact response tier
  for request logging only.
- `app/modules/api_keys/*`, `app/db/*`, `app/modules/request_logs/*`, pricing,
  metrics, dashboards, and rollups persist policy configuration or report
  observed tiers; they do not build upstream request payloads.

## OpenSpec validation evidence

The change and its primary owning capability pass strict validation:

- `npx --yes @fission-ai/openspec@1.3.0 validate prohibit-priority-service-tier --type change --strict`
- `npx --yes @fission-ai/openspec@1.3.0 validate fast-mode-policy --type spec --strict`

The aggregate command
`npx --yes @fission-ai/openspec@1.3.0 validate --specs --strict` reports
`50 passed, 8 failed (58 items)`. The failing capabilities are the pre-existing
`database-backends`, `frontend-architecture`, `model-catalog-compat`,
`model-source-routing`, `query-caching`, `responses-api-compat`,
`usage-error-metrics`, and `usage-refresh-policy` specs. The new
`responses-api-compat` requirement itself contains the required normative
keyword and scenarios; this change does not broaden into repairing the older
unrelated requirements in that capability.

## CodeRabbit review triage

The final light review completed against all 18 changed files with zero
findings. A prior completed pass identified one minor observability issue:
warmup strip logs used the ambient request ID rather than the per-submission
upstream ID. The helper now accepts the submission ID and the warmup regression
test proves that the strip log matches `x-request-id`.

Two interrupted earlier streams suggested changing omission assertions to
literal `service_tier: "default"`, and another suggested making that the
normative wire behavior. Those suggestions were deliberately not applied:
the existing API-key contract and issue #546 document that the ChatGPT/Codex
backend rejects literal `auto`/`default`, while omission is its established
default-tier representation. One valid minor from an interrupted stream asked
for auditable aggregate OpenSpec evidence; the command and baseline failures
are recorded above.
