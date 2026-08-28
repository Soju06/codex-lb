## Context

The model registry aggregates general per-plan and per-account Codex catalogs. Some separately metered models, including `gpt-5.3-codex-spark`, can be omitted from that general per-account catalog while active accounts continue to report fresh model-specific additional-quota telemetry. Account selection currently applies the exact per-account catalog filter before loading additional-quota data, so an authoritative empty account index rejects the request before stronger model-specific evidence is evaluated.

## Goals / Non-Goals

**Goals:**
- Let fresh model-specific additional-quota telemetry establish account-level support for mapped, separately metered models.
- Preserve registry plan and service-tier restrictions.
- Preserve fail-closed behavior for stale, missing, or exhausted additional-quota data and all existing health, cooldown, capacity, and routing gates.
- Preserve HTTP bridge session reuse for an account admitted by that evidence without performing quota repository I/O in the synchronous reuse predicate.

**Non-Goals:**
- Do not add a generic fallback for unknown or unmapped models.
- Do not change model discovery endpoints or reinsert Spark into an authoritative general catalog.
- Do not change quota persistence, refresh cadence, or API schemas.

## Decisions

For a model mapped by the additional-quota registry, account candidate filtering resolves the authoritative general per-account model index independently from requested service-tier filtering. Accounts present in that general model index stay in the normal pool only when they also pass the authoritative per-account service-tier index. Only accounts genuinely absent from the general model index may use the omission fallback, which applies the registry's allowed plan or service-tier plans before requiring fresh model-specific quota telemetry.

This keeps the exception narrow and evidence-backed. A catalog-supported account rejected by the requested account-level service tier cannot be reclassified as model-catalog-omitted or restored by quota evidence. Treating bootstrap metadata as globally authoritative would reject genuinely omitted-but-usable models, while bypassing every model or tier filter would admit unsupported accounts.

Explicit caller-supplied additional-limit filters do not activate this exception. Only a canonical model-to-quota mapping can override the general account catalog, preventing an unrelated quota key from bypassing model support.

After final account selection, the selector records a frozen admission value only when the selected account ID belongs to the quota-admitted catalog-omission set. The value contains the normalized requested model, canonical quota key, and normalized effective service tier. HTTP bridge creation and reconnect copy that optional value with the selected account.

The synchronous bridge request-compatibility predicate is applied before every existing-session return, including normal key lookup, previous-response alias fallback, and in-flight creation waiters. It may treat a current general account-catalog omission as admissible only when the session's recorded value exactly matches the requested model, its current canonical quota mapping, and effective service tier. A catalog-supported account continues through the normal account-level service-tier logic even if an older session carries omission provenance. Reconnect replaces or clears the value together with the newly selected account, so failover cannot inherit another selection's exception.

For a genuine general-catalog omission, every reuse check also re-evaluates the registry's current plan or requested service-tier plan eligibility. Provenance records the quota-backed omission decision but does not freeze an account's former plan eligibility. Compatibility failure is local to the current request: an unanchored request receives a collision-resistant request-scope fork, while an anchored request fails closed. Neither outcome detaches, closes, replaces, or rewrites the shared live session.

When a request has already been forwarded to the canonical owner and compatibility creates an `internal_request_parallel` fork, that request-local fork stays on the receiving owner even if its derived key rendezvous-hashes to another replica. The local-ownership marker is bound to that exact derived fork key and is assigned from both the existing-session mismatch and in-flight-waiter mismatch paths, preventing a second hop without changing ownership for canonical keys, ordinary unforwarded forks, or later unrelated key iterations.

Live alias targets are preserved on request incompatibility. Previous-response and turn-state index entries are removed only when their target session is missing, closed, or inactive; a mismatch leaves those indexes, the session registry, close scheduling, and stored request model and service tier unchanged so a later compatible request can still resolve the owner.

## Risks / Trade-offs

- A malformed additional-quota registry could map a model incorrectly. Existing canonical registry loading, plan applicability, and fresh-data requirements constrain that risk.
- Service-tier account affinity is less precise only for an account genuinely omitted from the general model catalog, because no model-specific account-tier entry exists to trust for that account. Current plan-level service-tier filtering remains authoritative for that fallback during both selection and reuse; catalog-supported accounts continue to require the exact per-account service-tier index.
- Fresh quota telemetry may temporarily disappear during refresh failures. Selection remains fail-closed with the existing additional-quota data-unavailable error.
- Bridge reuse depends on a prior successful selection rather than re-reading quota telemetry. Binding the immutable admission value to the selected model, quota key, and service tier keeps that lifecycle exception no broader than the original selection decision.
