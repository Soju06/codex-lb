# Tasks — Smart HTTP→upstream transport routing

## 1. Config / Settings

- [x] 1.1 Add `http_downstream_transport_policy` to `Settings` (and
  dashboard settings if surfaced there), enum
  `smart | always_http | always_websocket | pinned`, default `smart`.
- [x] 1.2 Treat `pinned` as an alias of `always_http` at resolution time.
- [x] 1.3 Unit test: setting parses, defaults to `smart`, rejects unknown
  values.

## 2. Transport decision (core)

- [x] 2.1 In `app/modules/proxy/service.py` `_stream_with_retry`, replace
  the unconditional pin:
  ```python
  if request_transport == _REQUEST_TRANSPORT_HTTP and upstream_stream_transport == "websocket":
      upstream_stream_transport = "http"
  ```
  with a policy-aware decision that runs only when the base transport
  resolved to `"websocket"` and `request_transport == http`.
- [x] 2.2 Add a pure helper (e.g.
  `_resolve_http_downstream_transport(policy, *, payload, headers) -> str`)
  that returns `"http"` or `"websocket"`:
  - `always_http` / `pinned` → `"http"`
  - `always_websocket` → `"websocket"`
  - `smart` → `"websocket"` iff any sticky signal present, else `"http"`.
- [x] 2.3 Sticky-signal detection MUST reuse existing helpers from
  `app/modules/proxy/affinity.py`:
  `_prompt_cache_key_from_request_model(payload)`,
  `_sticky_key_from_session_header(headers)`,
  `_sticky_key_from_turn_state_header(headers)`, and
  `payload.previous_response_id`. Do not re-implement header parsing.
- [x] 2.4 Resolve the effective policy = per-key override (if non-null)
  else global setting. Thread the authenticating `api_key` into the
  decision (already passed into `_stream_with_retry`).
- [x] 2.5 Keep higher-precedence rails ABOVE the policy: explicit
  `upstream_stream_transport` override, oversized-payload bypass, and
  image / image-generation bypass all still force HTTP before the policy
  is consulted. Native WS clients (`request_transport == websocket`) are
  never touched.
- [x] 2.6 Add a structured log line recording the chosen policy, the
  sticky-signal verdict, and the final upstream transport for triage.

## 3. API key field + schema + migration

- [x] 3.1 Add nullable `transport_policy_override` column to the API key
  model (values `smart | always_http | always_websocket`, null = follow
  global).
- [x] 3.2 Alembic migration: additive nullable column on the current
  single head; downgrade drops the column. Existing rows default null.
  Verify the revision sits on the intended parent (single upgrade head).
- [x] 3.3 Extend create (`POST /api/api-keys`) and update
  (`PATCH /api/api-keys/{id}`) schemas to accept
  `transportPolicyOverride` (optional / nullable); validate the enum and
  return 400 on bad values.
- [x] 3.4 Return `transportPolicyOverride` on key read serialization.
- [x] 3.5 Unit/integration tests for create-with-override,
  update-set/clear override, invalid-value rejection, and read
  round-trip.

## 4. Dashboard UI

- [x] 4.1 Global Settings: add the `http_downstream_transport_policy`
  dropdown (`smart` default) with concise operator-facing labels (no
  internal transport jargon dumped raw).
- [x] 4.2 API-key create/edit: add a `transportPolicyOverride` control
  with an explicit "Follow global default" (null) option plus the three
  policy values.
- [x] 4.3 Frontend tests for the new controls (render + submit).

## 5. Spec sync + validation

- [x] 5.1 Keep `responses-api-compat` and `api-keys` delta specs in sync
  with the implemented behavior.
- [x] 5.2 Add/refresh `openspec/specs/responses-api-compat/context.md`
  (or change-level context) with the rationale: 99.9% overload-before-
  first-token evidence, WS handshake + admission cost, prewarm
  amortization on multi-turn only, and the single-shot vs sticky split.
- [x] 5.3 Run `openspec validate smart-http-transport-routing --strict`
  and require `is valid`.

## 6. Tests (behavioral)

- [x] 6.1 Transport-decision unit tests covering every scenario in the
  `responses-api-compat` delta: smart single-shot→HTTP, smart
  sticky→WS (one test per signal: previous_response_id, prompt_cache_key,
  session header, turn-state header), always_http pin, always_websocket
  keep, per-key override wins, null override follows global, explicit
  websocket override beats policy, oversized-payload bypass under
  always_websocket, native WS untouched.
- [x] 6.2 Integration test at the `/v1/responses` route proving a
  single-shot HTTP request goes upstream HTTP and a sticky one goes
  upstream WS under the default `smart` policy.
- [x] 6.3 Regression: confirm native WebSocket client path is byte-for-
  byte unchanged.

## 7. Pre-push gate

- [x] 7.1 `uvx ruff check . && uvx ruff format --check .`
- [x] 7.2 `uv run ty check` (revert any incidental `uv.lock` churn).
- [x] 7.3 Targeted proxy/responses test sweep, then broader sweep.
