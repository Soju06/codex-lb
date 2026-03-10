# Design: Separate compact transport contracts from normal Responses transport

## Context

PR `#169` and the follow-up compact workaround exposed a structural problem: compact requests were treated as if they were just a timeout-sensitive flavor of normal Responses. That assumption is false.

The upstream compact endpoint owns a different contract:

- it returns a compaction window, not a standard `response.completed` payload
- the returned window is opaque and provider-managed
- the returned window is the canonical input for the next `/responses` request

Once the proxy rerouted compact through `/codex/responses` and rebuilt a normal response object, it stopped preserving that contract. The proxy became more available for one transport failure mode, but less correct for long-running agent state.

## Change Contract

Inputs:
- inbound `/backend-api/codex/responses/compact` and `/v1/responses/compact` requests
- upstream compact JSON payloads that may include provider-owned compaction items and retained context items
- transient transport failures from the upstream compact endpoint

Outputs:
- direct compact responses returned as canonical next context windows
- explicit transport errors when direct compact execution fails
- tracing that makes it clear compact used the dedicated contract path

Invariants:
- compact success must preserve provider-owned payload semantics
- compact output must remain opaque to proxy business logic
- silent semantic fallback is forbidden
- ordinary `/responses` transport behavior remains independent

Constraints:
- keep existing routing, affinity, refresh/retry, request logging, and API key settlement behavior in `ProxyService`
- do not use boolean mode switches on a shared transport abstraction for compact vs non-compact semantics
- prefer fail-closed behavior over surrogate fallback when correctness is at risk
- allow bounded retries only when the retry remains inside the compact contract and the failure happened before compact success semantics became ambiguous

## Decision

### 1. Split transport responsibilities by contract

Introduce two distinct low-level transport responsibilities:

- `ResponsesSessionTransport` for normal `/responses` flows, where streaming, websocket support, and SSE assembly are valid concerns
- `CompactCommandTransport` for `/responses/compact`, where the only valid success path is direct compact execution and opaque JSON pass-through

The split is by upstream contract, not by protocol alone. HTTP vs WebSocket is a transport detail; compact vs responses is a semantic boundary.

### 2. Treat compact output as a first-class opaque payload

Compact success responses should no longer be modeled as if they were ordinary `OpenAIResponsePayload` instances with a few extra fields. The implementation should use a dedicated permissive compact payload type, or an equivalent pass-through object with minimal validation, so the code clearly communicates that compact results are canonical opaque windows.

This keeps provider-owned fields such as encrypted compaction state and retained context items intact and prevents later "normalization" work from quietly rewriting them.

### 3. Fail closed on compact transport failures without surrogate fallback

If direct `/codex/responses/compact` fails before returning a valid compact JSON payload, the proxy must not change contracts. It must not:

- call `/codex/responses` as a surrogate
- reconstruct a compact window from streamed `response.completed` events
- synthesize local compaction items

The proxy MAY perform a bounded retry only against the same `/codex/responses/compact` contract when the failure is still in a provably safe transport phase, such as:

- `401 -> refresh -> retry`
- connect, TLS, or other pre-body transport failure
- upstream `502`, `503`, or `504` before any valid compact JSON payload has been accepted

The proxy MUST NOT automatically replay compact requests after ambiguous mid-flight failures, invalid compact JSON, or any case where compact success semantics are no longer trustworthy.

This is the safer default because a semantically wrong compact response is worse than a visible transport error, while a bounded same-contract retry can still improve availability without breaking semantics.

### 4. Keep orchestration logic where it already belongs

`ProxyService.compact_responses()` should remain the orchestration layer for:

- account selection and affinity
- `401 -> refresh -> retry`
- request logging
- API key reservation and settlement

The refactor should narrow low-level transport responsibilities, not duplicate load-balancer logic.

### 5. Add compact-boundary observability

Compact request traces should record enough information to prove that the dedicated contract path was used. At minimum:

- upstream target path
- success payload object type when present
- transport failure phase when present
- retry attempt when present
- affinity source when present
- an explicit indication that no surrogate fallback was attempted

This makes future regressions diagnosable without reading code diffs.

## Rejected Alternatives

### Keep buffered `/codex/responses` as an automatic fallback

Rejected because it preserves availability at the cost of returning a semantically wrong object. This is silent data corruption for agent context.

### Fail immediately without any bounded same-contract retry

Rejected because the observed problem includes path-specific transport failures on compact requests. A bounded retry inside the same compact contract can reduce visible `502` errors without crossing the semantic boundary that caused the regression.

### Share one transport implementation with a `compact=True/False` switch

Rejected because the bug came from exactly this kind of conceptual mixing. The two paths have different success objects, retry boundaries, and correctness rules.

### Parse compact results into the normal Responses payload model

Rejected because it obscures the contract boundary and invites future code to treat compact output as a normal response object again.

## Rollout Notes

- This change should stack on top of the direct compact pass-through fix, not replace it.
- If `restore-codex-compact-semantics` is still unmerged when implementation starts, its direct compact behavior should be folded in before the transport split is completed.
