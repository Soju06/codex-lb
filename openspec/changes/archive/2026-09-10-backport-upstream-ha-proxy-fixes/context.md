## Scope and provenance

Local baseline: 7ecb38de (key-dashboard commit, pushed to fork). Upstream main observed 2026-09-10: 0f6a31c5; latest released prerelease remains v1.25.0-beta.6 (e4c0164d). Selected fixes:

- 90e98703 / https://github.com/Soju06/codex-lb/pull/2174 — every-replica client-version warmup.
- 0083bc63 / https://github.com/Soju06/codex-lb/pull/2242 — trusted subscription routing hints.
- 5be82ef4 / https://github.com/Soju06/codex-lb/commit/5be82ef40d5b63ae1c54c8e25e2e0cccb5caef39 — rebuilt HTTP hop-by-hop sanitation.
- 2a430349 / https://github.com/Soju06/codex-lb/pull/2240 — code-less HTTP 429 burst backoff.

## Rationale and boundaries

A full beta.6 merge conflicts with the fork's native transport and adds multiple migrations, settings removals, and unrelated dashboard changes. This bounded backport leaves those for a separately reviewed upgrade. In particular, never edit the already-deployed usage-group migration's parent to integrate upstream; a future full merge needs an additive merge revision.

## Example and failure modes

An HTTP request carrying encrypted reasoning is bound to account A. A code-less 429 pauses that request for 1, 2, then 4 seconds while retaining A and holding HTTP headers; a final rejection remains 429 with upstream Retry-After or 5. Fresh movable traffic may prefer account B during A's local burst cooldown. A coded quota rejection retains the existing quota path.

An inbound `x-codex-routing-hint: model=forged;tier=priority` cannot override a normalized body choosing model `gpt-5.4` without a tier: the synthesized hint is `model=gpt-5.4`. Connection-nominated identity headers are discarded before classifying native clients.

## Operations and validation

Do not touch production or unrelated installer work. Preserve key-dashboard, UTC+7 API-key resets, client ingress and native output budgets, ownership and settlement invariants. Record exact local tests and limitations in verification.md. New code is not committed, pushed, or deployed as part of the separate key-dashboard push authorization.

## Integration adaptations

- Kept beta.5/fork `get_settings()` and deterministic-failover configuration seams instead of importing beta.6's dashboard-override refactor.
- Adapted the model-source test session seam to this baseline's `lease_http_session`; no provider transport behavior changed.
- New regression tests exposed pre-settlement health writes on exhausted keyed bursts. Only burst terminal failures now use the existing deferred-health queue; the non-burst 401 refresh path is retained. Confirmed settlement or release is required before the terminal burst penalty.
- Verified canonical backend/v1 and trailing-slash behavior. Compact trailing-slash URLs already return 405 due to unchanged route registration; tests explicitly preserve that error envelope and zero upstream calls rather than silently adding aliases.
- Kept the architecture ratchet unchanged by simplifying the existing error-code normalization call in the service facade; no threshold was raised.
